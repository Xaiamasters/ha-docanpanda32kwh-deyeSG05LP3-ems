/* Docan Panda & Deye EMS contributors. MIT. Independent cards receive an observation-only facade. */
const kind=location.hash.slice(1); const host=document.getElementById('card'); let card,loading=false,signature,pending;
if(kind==='map'){
 document.body.classList.add('map');const credit=document.createElement('footer');credit.className='attribution';
 credit.innerHTML='<a href="https://openfreemap.org/" target="_blank" rel="noopener">OpenFreeMap</a> · © <a href="https://openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a><br>Weather: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a> · NL elevation: <a href="https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn" target="_blank" rel="noopener">AHN / PDOK</a> · Elevation: <a href="https://geoservices.ign.fr/" target="_blank" rel="noopener">IGN / Géoplateforme</a>';
 document.body.append(credit);
}
window.customCards=[];
// Ha-card is normally provided by the parent HA frontend. Keep the independent
// document's host minimal; no HA authenticated connection is passed to it.
if(!customElements.get('ha-card'))customElements.define('ha-card',class extends HTMLElement{
  connectedCallback(){this.style.display='block';this.style.width='100%';this.style.height='100%';this.style.minHeight='0';this.style.boxSizing='border-box';}
});
// Map controls change only its view. Standalone frames do not inherit HA icons.
if(!customElements.get('ha-icon'))customElements.define('ha-icon',class extends HTMLElement{
 static get observedAttributes(){return ['icon'];}
 connectedCallback(){this.render();}attributeChangedCallback(){this.render();}
 render(){const name=(this.getAttribute('icon')||'').replace('mdi:','');const icons={'home':'⌂','lock':'🔒','lock-open':'🔓','lock-open-variant':'🔓','white-balance-sunny':'☀','weather-sunny':'☀','weather-cloudy':'☁','cloud':'☁','close':'×','cog':'⚙','fullscreen':'⛶','fullscreen-exit':'⛶','compass':'✥','compass-outline':'✥','rotate-3d-variant':'⟳','rotate-orbit':'⟳','layers-outline':'▱','chevron-left':'‹','chevron-right':'›','circle-outline':'○','circle':'●'};this.textContent=icons[name]||'◇';this.style.display='inline-flex';this.style.alignItems='center';this.style.justifyContent='center';this.style.width='24px';this.style.height='24px';this.style.fontSize='20px';}
});
function fail(text){host.replaceChildren();card=null;signature=null;const note=document.createElement('div');note.className='note';note.textContent=text;host.append(note);}
function readonly(message){
  const o=message.options;
  return {states:message.states,language:message.language,locale:{language:message.language,number_format:'language',time_format:'language'},
    config:{latitude:o.latitude,longitude:o.longitude,time_zone:o.time_zone||Intl.DateTimeFormat().resolvedOptions().timeZone,unit_system:{length:'km',temperature:'°C'}},
    themes:{darkMode:true,default_theme:'default',themes:{}},localize:()=>'',user:{is_admin:false},
    callService:()=>Promise.reject(new Error('Read-only visualization')),
    callApi:()=>Promise.reject(new Error('No authenticated API in visualization')),
    callWS:()=>Promise.reject(new Error('No authenticated connection in visualization'))};
}
async function render(message){
  pending=message;if(loading)return;const options=message.options;
  if(kind==='map'&&(!options.map_enabled||!Number.isFinite(options.latitude)||!Number.isFinite(options.longitude)))return;
  if(kind==='flow'&&!['solar_power','inverter_power','load_power','grid_power','battery_power','battery_soc','battery_voltage','battery_current'].every(k=>options[k])){
    fail('Choose your solar, inverter, home, grid and battery measurements in Docan Panda & Deye EMS settings to show the power flow.');return;
  }
  if(kind==='flow'&&(!Number.isFinite(options.battery_capacity)||!Number.isFinite(options.battery_shutdown_soc))){fail('Enter your battery capacity and diagram threshold in Docan Panda & Deye EMS settings to show the power flow.');return;}
  const valid=(m,k)=>{const s=m.states[m.options[k]];return s&&s.state.trim()!==''&&Number.isFinite(Number(s.state));};
  if(kind==='flow'&&!['inverter_power','load_power','grid_power','battery_power','battery_soc','battery_voltage','battery_current'].every(k=>valid(message,k))){
    fail('Power flow is unavailable while a required battery, inverter, home or grid measurement is missing.');return;
  }
  // The vendor diagram assumes DC-connected PV. AC and mixed installations use
  // an explicit aggregate diagram instead of drawing AC production onto DC MPPTs.
  if(kind==='flow'&&options.solar_connection!=='deye_dc'){
    const watts=k=>{const v=Number(message.states[options[k]]?.state);return Number.isFinite(v)?(Math.abs(v)>=1000?(v/1000).toFixed(2)+' kW':v.toFixed(0)+' W'):'Unavailable';};
    const solar=options.solar_connection==='none'?'No solar configured':`${options.solar_connection==='external_ac'?'AC solar':'Combined AC/DC solar'}: ${watts('solar_power')}`;
    host.innerHTML=`<svg viewBox="0 0 760 320" role="img" aria-label="Read-only aggregate power flow" style="width:100%;height:100%;font:16px system-ui;color:#dcebf5"><g fill="none" stroke="#538cad" stroke-width="3"><path d="M155 190 H335 M420 190 H610 M420 190 V70 H610"/><path d="M420 70 V190" stroke-dasharray="8 5"/></g><g fill="#0c2535" stroke="#2c6684"><rect x="25" y="135" width="170" height="100" rx="12"/><rect x="280" y="135" width="170" height="100" rx="12"/><rect x="570" y="135" width="165" height="100" rx="12"/><rect x="570" y="25" width="165" height="90" rx="12"/></g><g fill="#dcebf5" text-anchor="middle"><text x="110" y="168">Battery</text><text x="110" y="197" fill="#80bbff">${watts('battery_power')}</text><text x="110" y="221" font-size="12">Positive = discharge</text><text x="365" y="168">Deye AC</text><text x="365" y="198" fill="#67e8f9">${watts('inverter_power')}</text><text x="650" y="168">Home</text><text x="650" y="198" fill="#ff9746">${watts('load_power')}</text><text x="650" y="58">Grid</text><text x="650" y="85" fill="#34d399">${watts('grid_power')}</text><text x="210" y="46" fill="#facc15">${solar}</text><text x="210" y="74" font-size="12">${options.solar_connection==='mixed'?'AC/DC production split is not configured.':options.solar_connection==='external_ac'?'Production joins the household AC network.':''}</text><text x="380" y="282" font-size="12">Aggregate measurements · positive grid = import · no equipment controls</text></g></svg>`;
    card=null;signature=null;return;
  }
  loading=true;
  try {
    if(!card){await import(kind==='map'?'./vendor/helios.js':'./vendor/sunsynk-power-flow-card.js');}
    const current=pending;const o=current.options;
    const solarKeys=['solar_power',...['solar_1_power','solar_2_power','solar_3_power'].filter(k=>o[k])];
    const solarAvailable=kind!=='flow'||solarKeys.every(k=>valid(current,k));
    let notice=document.getElementById('flow-availability');
    if(kind==='flow'&&!notice){notice=document.createElement('div');notice.id='flow-availability';notice.setAttribute('role','status');notice.style.cssText='position:absolute;top:6px;left:10px;right:10px;text-align:center;font:12px/1.5 system-ui;color:#facc15;z-index:2';document.body.append(notice);}
    if(notice){notice.textContent=solarAvailable?'':'Solar measurements unavailable — solar flow is hidden. Battery, home and grid readings are live.';notice.hidden=solarAvailable;}
    const extras={battery_stored_energy:null,battery_soh:'battery_soh',battery_charge_today:'day_battery_charge_70',battery_discharge_today:'day_battery_discharge_71',inverter_state:'inverter_status_59',inverter_temperature:'radiator_temp_91',battery_temperature:'dc_transformer_temp_90',import_today:'day_grid_import_76',export_today:'day_grid_export_77',solar_today:'day_pv_energy_108'};
    const additional=Object.fromEntries(Object.entries(extras).filter(([key,dest])=>dest&&o[key]&&current.states[o[key]]&&!['unknown','unavailable'].includes(current.states[o[key]].state)).map(([key,dest])=>[dest,o[key]]));
    const config=kind==='map'?{type:'custom:helios-card','home-latitude':o.latitude,'home-longitude':o.longitude}:
      {type:'custom:sunsynk-power-flow-card',cardstyle:'full',wide:true,dynamic_line_width:true,show_solar:solarAvailable,show_battery:true,show_grid:true,show_nonessential:false,show_aux:false,
       inverter:{model:'deye',modern:true,colour:'#67e8f9'},battery:{energy:o.battery_capacity*1000,shutdown_soc:o.battery_shutdown_soc,show_absolute:true,show_daily:!!(additional.day_battery_charge_70&&additional.day_battery_discharge_71),colour:'#22c55e',charge_colour:'#38bdf8',...(o.battery_max_power?{max_power:o.battery_max_power}:{})},
       solar:{mppts:o.solar_3_power?3:o.solar_2_power?2:1,show_daily:!!additional.day_pv_energy_108,display_mode:1,auto_scale:true,colour:'#facc15',pv1_name:o.solar_1_name||'Solar 1',pv2_name:o.solar_2_name||'Solar 2',pv3_name:o.solar_3_name||'Solar 3',...(o.solar_max_power?{max_power:o.solar_max_power,pv1_max_power:o.solar_max_power}:{}),...(o.solar_3_max_power?{pv3_max_power:o.solar_3_max_power}:{})},load:{colour:'#f8fafc',dynamic_icon:true,show_daily:false},grid:{grid_name:o.grid_name||'Grid',colour:'#60a5fa',export_colour:'#fbbf24',show_nonessential:false,show_daily_buy:!!additional.day_grid_import_76,show_daily_sell:!!additional.day_grid_export_77},
       entities:{...additional,...(!o.solar_1_power?{pv_total:o.solar_power}:{}),pv1_power_186:o.solar_1_power||o.solar_power,...(o.solar_2_power?{pv2_power_187:o.solar_2_power}:{}),...(o.solar_3_power?{pv3_power_188:o.solar_3_power}:{}),essential_power:o.load_power,inverter_power_175:o.inverter_power,grid_ct_power_172:o.grid_power,
         grid_ct_power_total:o.grid_power,grid_power_169:'none',battery_power_190:o.battery_power,
         battery_soc_184:o.battery_soc,...(o.battery_voltage?{battery_voltage_183:o.battery_voltage}:{}),...(o.battery_current?{battery_current_191:o.battery_current}:{})}};
    if(!card){card=document.createElement(kind==='map'?'helios-card':'sunsynk-power-flow-card');host.className=kind;host.replaceChildren(card);}
    const next=JSON.stringify(config);if(signature!==next){card.setConfig(config);signature=next;}
    card.hass=readonly(current);
  } catch (error) {console.warn('Docan Panda & Deye EMS visualization:',error.message);fail('Visualization unavailable. Current measurements remain visible on the dashboard.');card=null;}
  finally{loading=false;}
}
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==parent||event.data?.type!=='docan-deye-card-state'||event.data.kind!==kind)return;
  render(event.data);
});
document.addEventListener('hass-more-info',e=>e.stopImmediatePropagation(),true);
parent.postMessage({type:'docan-deye-card-ready'},location.origin);
