"""Diagnostics intentionally omit location, identifiers and credentials."""


async def async_get_config_entry_diagnostics(hass, entry):
    c = entry.runtime_data
    control=c.data.get('control',{})
    return {'version': c.data['version'], 'mode': c.data['mode'], 'physical_authority': c.data['physical_authority'],
            'commissioned':control.get('commissioned',False),'stop_present':bool(control.get('stop')),
            'watchdogs':control.get('watchdogs',{}),
            'model': c.settings['model'], 'provider': c.settings['pricing']['provider'],
            'connection': c.settings['connection'],
            'configured_measurements': sorted(c.data['values']), 'ready': c.data['ready'],
            'errors': c.data['errors'], 'plan_mode': c.data['plan']['mode'],
            'plan_status': c.data['plan']['status']}
