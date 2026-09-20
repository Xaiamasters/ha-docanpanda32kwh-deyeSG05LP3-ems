"""Production-policy decisions and failure handling, using an in-memory plant."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'custom_components/docan_deye_ems'))
from engine.controller import Controller
from engine.context import dated_contract,pinned_exports
from engine.daily import build_day,repin_draws
from engine.planner import Contract,nightly_reserve,plan_day
from engine.programs import plan_to_programs
from engine.runtime import ShadowRuntime,validate_pin
from engine.safety import charge_stop,export_stop
from engine.simulation import SimulatedPlant,example_snapshot
from engine.storage import MemoryState,FileState

AT=datetime(2026,9,20,12,20)


def pin(**kwargs):
    return {'for_date':'2026-09-20','ceiling_pct':95,'export_clusters':[[76,80,64]],'reserve':{'pct':64},**kwargs}


class EngineTests(unittest.TestCase):
    def controller(self,**changes):
        snapshot=example_snapshot(AT);snapshot.update(changes)
        plant=SimulatedPlant(snapshot);state=MemoryState()
        return Controller(AT,plant,state,pin()),plant,state

    def test_shadow_computes_charge_but_never_writes(self):
        c,plant,state=self.controller()
        c.tick(False)
        self.assertEqual(state.state['action'],'charge')
        self.assertGreater(state.state['would_write'],0)
        self.assertEqual(plant.writes,[])

    def test_export_tariff_and_power_are_explicit_inputs(self):
        at=AT.replace(hour=19)
        contract=replace(Contract(),export_power_w=6000,export_price_deduction=.05,degradation=.03)
        plant=SimulatedPlant(example_snapshot(at,soc=85))
        c=Controller(at,plant,MemoryState(),pin(),contract)
        self.assertAlmostEqual(c.net_export_value(.4,contract),.32)
        vector,_=c.target_vector('export',plant.snapshot())
        self.assertEqual(vector['export_w'],6000)
        self.assertEqual(vector['grid_export_w'],6000)

    def test_program_time_readback_uses_logical_fields(self):
        from engine.programs import _readback_ok
        self.assertTrue(_readback_ok('12:00:00','program_2_time','12:00'))
        self.assertFalse(_readback_ok('12:15:00','program_2_time','12:00'))

    def test_ceiling_reaches_planner_and_hysteresis(self):
        c,plant,state=self.controller(soc=94,grid_charge_on=True)
        c.tick(False)
        self.assertEqual(state.state['action'],'charge')
        plant.data['grid_charge_on']=False
        c.tick(False)
        self.assertEqual(state.state['action'],'HOLD')
        plant.data['soc']=93
        c.tick(False)
        self.assertEqual(state.state['action'],'charge')

    def test_at_target_holds_voltage_inside_window(self):
        c,plant,state=self.controller(soc=95)
        action,_=c.decide(plant.snapshot(),Contract())
        vector,_=c.target_vector(action,plant.snapshot())
        self.assertEqual(action,'HOLD')
        self.assertEqual(vector['program_2_voltage'],55.2)
        self.assertEqual(vector['grid_charge'],'off')

    def test_pause_does_not_release_voltage_or_block_export(self):
        c,plant,_=self.controller(pause_charge=True)
        self.assertEqual(c.decide(plant.snapshot(),Contract())[0],'HOLD')
        c.at=AT.replace(hour=19)
        plant.data['soc']=85
        self.assertEqual(c.decide(plant.snapshot(),Contract())[0],'export')

    def test_pinned_export_and_reserve_override_replanning(self):
        c,plant,_=self.controller(soc=70)
        c.at=AT.replace(hour=19)
        self.assertEqual(c.decide(plant.snapshot(),Contract())[0],'export')
        plant.data['soc']=64
        self.assertEqual(c.decide(plant.snapshot(),Contract())[0],'IDLE')

    def test_rejects_short_charge_start_but_allows_inflight(self):
        c,plant,_=self.controller()
        c.at=AT.replace(hour=18,minute=28)
        self.assertTrue(c.start_guards(plant.snapshot(),None))
        plant.data['grid_charge_on']=True
        self.assertFalse(c.start_guards(plant.snapshot(),None))

    def test_stale_source_and_temperature_never_reverse_direction(self):
        c,plant,state=self.controller()
        plant.data['ages']['docan']=301
        c.tick(False)
        self.assertEqual(state.state['action'],'IDLE')
        self.assertIn('stale',state.state['reason'])
        plant.data['ages']['docan']=0
        plant.data['temps']['mos_temperature']=75
        c.tick(False)
        self.assertEqual(state.state['action'],'IDLE')

    def test_owner_stop_bypasses_broken_snapshot(self):
        c,plant,state=self.controller(stop=True)
        c.allow_writes=True
        plant.data['prices']=None
        c.tick(True)
        self.assertEqual(plant.writes[:4],[('solar_sell','off'),('grid_charge','off'),('work_mode','Zero Export To CT'),('energy_pattern','Load First')])
        self.assertFalse(state.state['physical_completion_proven'])

    def test_latched_stop_bypasses_all_snapshot_reads(self):
        c,plant,state=self.controller()
        c.allow_writes=True
        state.set_latch({'why':'operator review required'})
        with patch.object(plant,'snapshot',side_effect=AssertionError('must not read')):c.tick(True)
        self.assertEqual(len(plant.writes),4)

    def test_convergence_orders_reductions_before_enabling_export(self):
        c,plant,_=self.controller(grid_charge_on=True,grid_charge_a=160)
        c.allow_writes=True
        want,_=c.target_vector('export',plant.snapshot())
        ok,_=c.converge(plant.snapshot(),want,c.observed_vector(plant.snapshot(),want),'export')
        self.assertTrue(ok)
        self.assertEqual(plant.writes[0],('grid_charge','off'))
        self.assertEqual(plant.writes[-1],('solar_sell','on'))

    def test_readback_retry_does_not_repeat_a_write(self):
        c,plant,_=self.controller()
        c.allow_writes=True
        plant.stale_reads={'grid_charge':['off','off']}
        ok,_=c.converge(plant.snapshot(),{'grid_charge':'on'},{'grid_charge':'off'},'charge')
        self.assertTrue(ok)
        self.assertEqual(plant.writes,[('grid_charge','on')])
        self.assertEqual(plant.reads,['grid_charge']*3)

    def test_transport_failure_after_effect_latches_without_restoring_activity(self):
        c,plant,state=self.controller()
        c.allow_writes=True
        plant.fail_after_effect=1
        ok,_=c.converge(plant.snapshot(),{'grid_charge':'on'},{'grid_charge':'off'},'charge')
        self.assertFalse(ok)
        self.assertIn('DIRTY',state.latch['why'])
        self.assertEqual(plant.writes[1:],[('solar_sell','off'),('grid_charge','off'),('work_mode','Zero Export To CT'),('energy_pattern','Load First')])
        self.assertFalse(state.state['physical_completion_proven'])

    def test_write_intent_storage_failure_prevents_first_equipment_command(self):
        c,plant,state=self.controller()
        c.allow_writes=True
        with patch.object(state,'journal',side_effect=OSError('disk unavailable')):
            ok,_=c.converge(plant.snapshot(),{'grid_charge':'on'},{'grid_charge':'off'},'charge')
        self.assertFalse(ok)
        self.assertEqual(plant.writes,[])

    def test_stale_pin_reverts_to_base_ceiling_and_reserve(self):
        c,_,_=self.controller()
        c.DAY_PLAN=pin(for_date='2026-09-19',reserve={'pct':55})
        self.assertEqual(c.plan_ceiling()[0],90)
        self.assertEqual(c.contract_for_today(c.DAY_PLAN)[0].reserve_floor_pct,64)

    def test_invalid_ceiling_and_pin_rejected(self):
        c,_,_=self.controller()
        for bad in (True,float('nan'),96,-1):
            c.DAY_PLAN=pin(ceiling_pct=bad)
            with self.assertRaises(ValueError):c.plan_ceiling()
        with self.assertRaises(ValueError):validate_pin(pin(export_clusters=[[70,75,64],[74,80,64]]))

    def test_measured_reserve_formula_and_warmup(self):
        self.assertEqual(nightly_reserve([30]*8)['pct'],55)
        self.assertEqual(nightly_reserve([30]*3)['pct'],64)
        self.assertGreaterEqual(nightly_reserve([-5]*8)['pct'],25)

    def test_sound_high_floor_refuses_number_not_day(self):
        draws={'nights':[{}]*9,'admission':{'admitted_nights':9},'reserve':{'pct':71,'n':9,'p50':46,'p90':47}}
        adjusted,verdict,_=repin_draws(draws)
        self.assertEqual(verdict,'REFUSE_NUMBER')
        self.assertNotIn('pct',adjusted['reserve'])
        self.assertEqual(adjusted['reserve']['p50'],46)

    def test_daily_plan_has_joint_floor_ceiling_and_firmware_block(self):
        prices=example_snapshot(AT)['prices']
        draws={'for_date':'2026-09-20','reserve':{'pct':64,'p50':30,'p90':36,'n':9}}
        plan=build_day(prices,35,AT,AT.date(),draws)
        self.assertIn(plan['ceiling_pct'],(90,95))
        self.assertEqual(len(plan['programs']),6)
        self.assertEqual(plan['reserve']['pct'],64)
        for start,end,floor in plan['export_clusters']:
            self.assertGreaterEqual(start,72)
            self.assertGreaterEqual(floor,64)

    def test_96_slot_policy_refuses_dst_day_instead_of_misalignment(self):
        for size in (92,100):
            with self.assertRaises(ValueError):plan_day([.2]*size,35,Contract())

    def test_shadow_runtime_does_not_reuse_old_success_after_no_vector(self):
        state=MemoryState();runtime=ShadowRuntime(state)
        snapshot=example_snapshot(AT)
        result=runtime.run(snapshot,AT,snapshot['prices'],observed_plan=pin())
        self.assertEqual(result['status'],'charge')
        snapshot['programs'][1]['time']=None
        result=runtime.run(snapshot,AT,snapshot['prices'],observed_plan=pin())
        self.assertEqual(result['status'],'no_complete_target')
        self.assertFalse(result['physical_authority'])

    def test_scheduler_does_not_shift_missed_jobs_into_startup(self):
        state=MemoryState();runtime=ShadowRuntime(state)
        snapshot=example_snapshot(AT)
        result=runtime.run(snapshot,AT,snapshot['prices'],observed_plan=pin())
        self.assertEqual(result['scheduled_jobs'],{})

    def test_nightly_arm_records_missing_published_tomorrow(self):
        at=AT.replace(hour=23,minute=15)
        snapshot=example_snapshot(at)
        result=ShadowRuntime(MemoryState()).run(snapshot,at,snapshot['prices'],observed_plan=pin())
        self.assertEqual(result['schedule_error'],'scheduled_plan_not_ready')
        self.assertFalse(result['physical_authority'])

    def test_file_state_preserves_stop_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            state=FileState(path);state.set_latch({'why':'DIRTY'})
            self.assertEqual(FileState(path).latch['why'],'DIRTY')
            path.write_text('broken')
            self.assertIsNotNone(FileState(path).latch)

    def test_deadmen_apply_independent_bounds(self):
        snapshot=example_snapshot(AT)
        snapshot.update(grid_charge_on=True,pack_v=55.3)
        self.assertEqual(charge_stop(snapshot,5000),'charge_voltage_bound')
        snapshot.update(solar_sell_on=True,p1_w=-5000,pack_v=52,soc=42.3)
        self.assertEqual(export_stop(snapshot,AT),'export_soc_floor')
        snapshot['soc']=70
        self.assertEqual(export_stop(snapshot,AT.replace(hour=22,minute=30)),'export_hard_end')


if __name__=='__main__':unittest.main()
