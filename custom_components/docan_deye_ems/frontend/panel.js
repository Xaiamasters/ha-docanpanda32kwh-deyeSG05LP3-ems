/* Docan Panda & Deye EMS contributors. MIT. Authenticated dashboard and owner-controlled commissioning. */
const ROOT=new URL('.',import.meta.url).pathname;
const KEYS=['solar_power','load_power','grid_power'];
const COLORS=['#ffd12c','#ff9746','#33d7a4','#60a5fa'];
const UNITS={battery_soc:'%',battery_power:'W',battery_voltage:'V',battery_current:'A',inverter_power:'W',load_power:'W',grid_power:'W',solar_power:'W',solar_today:'kWh',load_today:'kWh',import_today:'kWh',export_today:'kWh'};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const numeric=value=>typeof value==='number'&&Number.isFinite(value);

class DocanDeyeEMSPanel extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this.charts=[];this.onMessage=this.onMessage.bind(this);}
 set hass(value){this._hass=value;if(!this.data)this.refresh();}
 set panel(value){const changed=value?.config?.entry_id!==this._panel?.config?.entry_id;this._panel=value;if(changed&&this.isConnected)this.initialize();}
 connectedCallback(){window.addEventListener('message',this.onMessage);this.initialize();}
 disconnectedCallback(){window.removeEventListener('message',this.onMessage);clearInterval(this.timer);this.generation=(this.generation||0)+1;this.charts.forEach(c=>c.destroy());this.forecastChart?.destroy();this.charts=[];}
 text(id,value){const e=this.shadowRoot.getElementById(id);if(e)e.textContent=value;}
 format(value,digits=0){return numeric(value)?value.toLocaleString(this._hass?.locale?.language||'en',{maximumFractionDigits:digits}):'—';}
 time(value){return new Date(value).toLocaleTimeString([],{timeZone:this.data?.time_zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'});}
 async initialize(){
  if(!this.isConnected||!this._panel)return;
  this.generation=(this.generation||0)+1;const gen=this.generation;
  clearInterval(this.timer);this.charts.forEach(c=>c.destroy());this.forecastChart?.destroy();this.charts=[];this.data=null;this.lastHistory=0;
  this.shadowRoot.innerHTML=`<link rel="stylesheet" href="${ROOT}panel.css?v=0.5.0-beta.1"><main>
   <header><div class="brand"><img class="project-icon" src="${ROOT}project-icon.png" alt=""><div><h1 id="site">Docan Panda & Deye EMS</h1><p>DOCAN PANDA · DEYE · ENERGY</p></div></div><div class="badges"><span id="header-soc">—</span><span id="header-price">—</span><span class="readonly" id="operation-mode">SHADOW</span><a class="settings" href="/config/integrations/integration/docan_deye_ems" aria-label="Household settings">⚙</a></div></header>
   <div class="notice" id="notice" role="status">Loading household observations…</div>
   <section class="hero"><article class="map"><div id="map-container" class="fill"></div></article>
    <article class="flow"><div class="heading"><h2>Power flow</h2><span id="topology">SELECTED MEASUREMENTS</span></div><div class="fill"><iframe title="Read-only power flow" data-kind="flow" src="${ROOT}card-frame.html?v=0.5.0-beta.1#flow"></iframe></div></article>
    <article class="battery"><div class="heading"><h2>Battery</h2><span id="battery-status">WAITING</span></div><div class="ring" id="ring"><div><strong id="soc">—</strong><small>DOCAN PANDA · 32 kWh</small></div></div><div class="power-line"><span>POWER</span><strong id="battery-power">—</strong></div><div class="battery-stats"><div><small>VOLTAGE</small><b id="voltage">—</b></div><div><small>CURRENT</small><b id="current">—</b></div><div><small>IN PACK · ESTIMATE</small><b id="in-pack">—</b></div></div></article></section>
   <section class="measurements"><article><div class="heading"><h2>Today</h2><span>REPORTED TOTALS</span></div>${[['Solar production','solar_today'],['Consumption','load_today'],['Grid import','import_today'],['Grid export','export_today']].map(([label,key])=>`<div class="total"><span>${label}</span><b id="${key}">—</b></div>`).join('')}<p class="muted">Unconfigured or unavailable totals stay blank.</p></article>
    ${['Solar','Home','Grid'].map((label,i)=>`<article><div class="heading"><h2>${label}</h2><span id="direction-${i}">LIVE</span></div><strong class="metric-value" style="color:${COLORS[i]}" id="power-${i}">—</strong><p class="muted">Selected household measurement</p><div class="chart"><canvas id="chart-${i}"></canvas><div class="chart-empty" id="empty-${i}">History is building</div></div></article>`).join('')}</section>
   <article class="plan"><div class="heading"><h2>Energy plan</h2><span id="plan-mode">SHADOW ONLY</span></div><p class="plan-status" id="plan-status">Waiting for inputs</p><div id="timeline"></div><p class="muted" id="assumptions"></p><p class="muted" id="authority-description">Planning preview. Commission the equipment before enabling live control.</p></article>
   <article class="plan" id="control-section"><div class="heading"><h2>Operation</h2><span id="control-mode">SHADOW</span></div><div id="control-body"></div><p id="control-message" role="status"></p></article>
   <article class="plan"><div class="heading"><h2>Electricity price</h2><span id="price-basis">HOUSEHOLD TARIFF</span></div><div class="chart"><canvas id="chart-3"></canvas><div class="chart-empty" id="empty-3">Waiting for a complete price day</div></div></article>
   <article class="plan" id="forecast-section"><div class="heading"><h2>Energy forecast & simulated schedule</h2><span>NO EQUIPMENT COMMANDS</span></div><p class="muted" id="forecast-range"></p><div class="forecast-summary" id="forecast-summary"></div><div class="forecast-chart"><canvas id="forecast-chart"></canvas></div><p class="muted" id="learning-status"></p></article>
   <article class="plan" id="statistics-section"><div class="heading"><h2>Measured grid costs & forecast accuracy</h2><span>PARTIAL COVERAGE IS SHOWN</span></div><p class="muted" id="forecast-errors"></p><div class="statistics-scroll"><table><thead><tr><th>Date</th><th>Observed hours</th><th>Import kWh</th><th>Export kWh</th><th>Net grid cost</th></tr></thead><tbody id="daily-statistics"></tbody></table></div><p class="muted">Whole-home telemetry accounting, not battery profit or guaranteed savings. Missing observations are not filled.</p></article>
   <footer><span id="freshness">Waiting for observations</span><span><button id="export-settings">Export household settings</button> · <a href="${ROOT}licenses.html" target="_blank" rel="noopener">Licenses & source</a> · <span id="version"></span></span></footer></main>`;
  this.shadowRoot.getElementById('export-settings').onclick=()=>this.exportSettings();
  try{
   await import('./vendor/chart.umd.js');
   if(gen!==this.generation||!this.isConnected)return;
   this.charts=COLORS.map((color,i)=>new globalThis.Chart(this.shadowRoot.getElementById(`chart-${i}`),{type:'line',data:{datasets:[{data:[],borderColor:color,borderWidth:1.6,pointRadius:0,spanGaps:false,stepped:i===3}]},options:{animation:false,responsive:true,maintainAspectRatio:false,parsing:false,plugins:{legend:{display:false},tooltip:{callbacks:{title:rows=>this.time(rows[0].parsed.x)}}},scales:{x:{type:'linear',ticks:{color:'#8095aa',maxTicksLimit:7,callback:v=>this.time(v)},grid:{display:false}},y:{ticks:{color:'#8095aa',maxTicksLimit:5},grid:{color:'#1c3143'}}}}}));
   this.forecastChart=new globalThis.Chart(this.shadowRoot.getElementById('forecast-chart'),{type:'line',data:{datasets:[]},options:{animation:false,responsive:true,maintainAspectRatio:false,parsing:false,plugins:{legend:{labels:{color:'#9bbace'}},tooltip:{callbacks:{title:rows=>new Date(rows[0].parsed.x).toLocaleString([],{timeZone:this.data?.time_zone})}}},scales:{x:{type:'linear',ticks:{color:'#8095aa',maxTicksLimit:10,callback:v=>this.time(v)}},y:{title:{display:true,text:'kW',color:'#8095aa'},ticks:{color:'#8095aa'}},soc:{position:'right',min:0,max:100,title:{display:true,text:'SoC %',color:'#8095aa'},ticks:{color:'#8095aa'},grid:{drawOnChartArea:false}}}}});
  }catch{this.text('notice','Charts could not load. Current measurements remain available.');}
  await this.refresh();
  if(gen===this.generation)this.timer=setInterval(()=>this.refresh(),30000);
 }
 async refresh(){
  const id=this._panel?.config?.entry_id;if(!id||!this._hass||!this.isConnected||this.busy)return;
  const gen=this.generation;this.busy=true;
  try{
   const data=await this._hass.callApi('GET',`docan_deye_ems/${encodeURIComponent(id)}/snapshot`);
   if(gen!==this.generation)return;
   this.data=data;this.render();await this.loadHistory();
  }catch{
   if(gen===this.generation){this.data={values:{},location:{enabled:false},solar:{},plan:{windows:[]},errors:{connection:'unavailable'}};this.render();this.text('notice','Connection unavailable. Live readings and the plan are hidden until observations return.');}
  }finally{this.busy=false;}
 }
 render(){
  if(!this.shadowRoot.getElementById('site'))return;
  const d=this.data,v=d.values||{},plan=d.plan||{},soc=v.battery_soc;
  this.text('site',d.name||'Docan Panda & Deye EMS');this.text('version',d.version||'');
  this.text('header-soc',numeric(soc)?`${this.format(soc,1)}% battery`:'Battery unavailable');
  this.text('soc',numeric(soc)?`${this.format(soc,1)}%`:'—');
  this.shadowRoot.getElementById('ring').style.setProperty('--soc',`${numeric(soc)?soc:0}%`);
  this.text('battery-status',!numeric(v.battery_power)?'UNAVAILABLE':v.battery_power>0?'DISCHARGING':v.battery_power<0?'CHARGING':'IDLE');
  for(const [id,key,unit,digits] of [['battery-power','battery_power','W',0],['voltage','battery_voltage','V',1],['current','battery_current','A',1]])this.text(id,`${this.format(v[key],digits)} ${unit}`);
  this.text('in-pack',numeric(soc)?`${this.format(soc*d.capacity_kwh/100,1)} kWh`:'—');
  for(const key of ['solar_today','load_today','import_today','export_today'])this.text(key,numeric(v[key])?`${this.format(v[key],2)} kWh`:'—');
  KEYS.forEach((key,i)=>this.text(`power-${i}`,numeric(v[key])?`${this.format(v[key])} W`:'—'));
  this.text('direction-2',!numeric(v.grid_power)?'UNAVAILABLE':v.grid_power<0?'EXPORTING':v.grid_power>0?'IMPORTING':'BALANCED');
  this.text('topology',({deye_dc:'DEYE DC SOLAR',external_ac:'EXTERNAL AC SOLAR',mixed:'COMBINED SOLAR',none:'NO SOLAR'})[d.solar?.connection]||'');
  const currency=d.prices?.currency||'';this.text('header-price',numeric(v.import_price)?`${this.format(v.import_price,3)} ${currency}/kWh`:'Price unavailable');
  const errors=Object.entries(d.errors||{});this.shadowRoot.getElementById('notice').hidden=!errors.length;
  this.text('notice',errors.map(([key,value])=>`${key.replaceAll('_',' ')}: ${value.replaceAll('_',' ')}`).join(' · '));
  this.text('plan-mode',d.physical_authority?'LIVE CONTROLLER':plan.mode==='production_stopped'?'CONTROLLER STOPPED':plan.mode==='production_shadow'?'AUTOMATIC ENGINE · SHADOW':plan.mode==='observed'?'OBSERVED CONTROLLER PLAN · READ ONLY':plan.mode==='forecast_shadow'?'FORECAST SIMULATION · NOT ARMED':'SHADOW ESTIMATE · NOT ARMED');
  this.text('plan-status',String(plan.status||'unavailable').replaceAll('_',' ')+(plan.mode?.startsWith('production_')&&plan.reason?' · '+plan.reason:''));
  this.shadowRoot.getElementById('timeline').innerHTML=(plan.windows||[]).length?plan.windows.map(w=>`<div class="window ${escape(w.action)}"><span>${escape(new Date(w.start).toLocaleDateString([],{timeZone:d.time_zone,month:'short',day:'numeric'}))} · ${escape(this.time(w.start))} – ${escape(this.time(w.end))}</span><div class="segment-bar"></div><strong>${escape(w.action.replaceAll('_',' '))}</strong>${w.price_origin==='anticipated'?'<small>Anticipated prices</small>':''}${numeric(w.import_price)?`<small>${escape(this.format(w.import_price,3))} ${escape(currency)}/kWh</small>`:''}</div>`).join(''):'<p class="muted">No valid plan windows to display.</p>';
  this.text('assumptions',(plan.assumptions||[]).join(' '));
  this.text('freshness',d.updated_at?`Updated ${this.time(d.updated_at)} · ${d.time_zone} · ${d.model}`:'No current snapshot');
  this.text('price-basis',`${(d.prices?.provider||'').toUpperCase()} · ${d.prices?.basis==='all_in'?'FINAL IMPORT TARIFF':'SPOT + HOUSEHOLD TAXES AND FEES'} · ${currency}/kWh`);
  for(const chart of this.charts)if(d.day){chart.options.scales.x.min=Date.parse(d.day.start);chart.options.scales.x.max=Date.parse(d.day.end);}
  if(this.charts[3]){
   const periods=d.prices?.periods||[],chart=this.charts[3];
   chart.data.datasets=[['Published import','#60a5fa',false],['Anticipated import','#ffd12c',true]].map(([label,color,anticipated])=>({label,borderColor:color,borderWidth:2,borderDash:anticipated?[6,5]:[],pointRadius:0,spanGaps:false,stepped:true,data:periods.flatMap(p=>[{x:Date.parse(p.start),y:(p.origin==='anticipated')===anticipated?p.import:null},{x:Date.parse(p.end)-1,y:(p.origin==='anticipated')===anticipated?p.import:null}])}));
   chart.options.plugins.legend={display:true,labels:{color:'#9bbace'}};
   chart.options.scales.x.ticks.callback=v=>new Date(v).toLocaleString([],{timeZone:d.time_zone,day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
   if(periods.length)chart.options.scales.x.max=Date.parse(periods.at(-1).end);
   chart.update('none');this.shadowRoot.getElementById('empty-3').hidden=!!periods.length;
  }
  const map=this.shadowRoot.getElementById('map-container');
  if(d.location?.enabled){if(!map.querySelector('iframe'))map.innerHTML=`<iframe title="Sun and location" data-kind="map" src="${ROOT}card-frame.html?v=0.5.0-beta.1#map"></iframe>`;}
  else map.innerHTML='<div class="map-off"><div class="sun-symbol">☀</div><h3>Sun & location</h3><p>Enable the map and select your home location in household settings.</p><small>Map and weather providers are contacted only after you opt in.</small></div>';
  this.renderControl();this.renderForecast();this.sendFrames();
 }
 renderControl(){
  const c=this.data?.control||{},active=!!c.active,mode=c.mode||'shadow';
  this.text('operation-mode',active?'LIVE CONTROL':mode.replaceAll('_',' ').toUpperCase());
  this.text('control-mode',active?'ACTIVE':mode.toUpperCase());
  this.text('authority-description',active?'The commissioned controller can change charging and export settings. Stop remains available below.':'The displayed plan is a preview. Equipment control is inactive.');
  const body=this.shadowRoot.getElementById('control-body');
  if(this.controlBusy)return;
  if(!c.available){body.innerHTML='<p>Live control requires direct Deye telemetry, an independent Docan USB/RS485 connection and the automatic charge and export engine. Configure these in integration settings.</p>';return;}
  if(this.commissioning){this.showCommissioning();return;}
  body.dataset.nonce='';
  const guards=Object.entries(c.watchdogs||{}).map(([k,v])=>`${k}: ${v?'ready':'not ready'}`).join(' · ');
  body.innerHTML=`<p>${escape(c.stop?'Stopped: '+c.stop.why:active?(c.controller?.reason||'Following the current plan.'):c.commissioned?'Commissioned and inactive. Enable live when you are ready.':'Start by checking the equipment and your limits.')}</p><p class="muted">${escape(guards)}</p><div class="control-actions">
    <button data-action="preview" ${active?'disabled':''}>Check equipment</button>
    <button data-action="enable_live" ${!c.commissioned||active||c.stop||!this.data.ready?'disabled':''}>Enable live control</button>
    <button data-action="stop" class="stop" ${!c.commissioned?'disabled':''}>Stop control</button>
    ${c.stop?'<button data-action="acknowledge_stop" '+(!c.commissioned?'disabled':'')+'>Acknowledge reviewed stop</button>':''}
    </div><p class="muted">Acknowledging a stop leaves control inactive. A restart or restored backup requires commissioning again. Guards on this HA host cannot operate if the whole host loses power.</p>`;
  body.querySelectorAll('[data-action]').forEach(button=>button.onclick=()=>this.controlAction(button.dataset.action,button.dataset.action==='acknowledge_stop'?{id:c.stop.id}:{}));
 }
 showCommissioning(){
  const p=this.commissioning,body=this.shadowRoot.getElementById('control-body');
  if(body.dataset.nonce===p.nonce)return;
  body.dataset.nonce=p.nonce;
  const labels={equipment_readings_match:'These readings match my inverter and battery displays.',bms_link_checked:'The battery-to-inverter BMS link is configured according to the equipment manuals.',sole_controller:'I have disabled every other writer to this inverter, including automations and vendor schedules that compete with this controller.',limits_checked:'I have checked the current, voltage, state of charge and temperature limits below.',grid_export_permission:'If export is enabled, my grid connection and electricity contract permit it.',host_failure_understood:'I understand that the inverter and BMS must enforce safe limits if HA or its host fails.'};
  body.innerHTML=`<p><strong>${escape(p.model)}</strong> · ${escape(p.soc)}% · ${escape(p.pack_v)} V · grid ${escape(p.grid_w)} W · battery ${escape(p.battery_w)} W</p>
   <dl class="commission-limits">${Object.entries(p.limits).map(([k,v])=>`<div><dt>${escape(k.replaceAll('_',' '))}</dt><dd>${escape(v)}</dd></div>`).join('')}</dl>
   <div class="commission-checks">${p.checks.map(k=>`<label><input type="checkbox" data-check="${escape(k)}"> <span>${escape(labels[k]||k)}</span></label>`).join('')}</div>
   <p class="muted">This check expires after two minutes. Confirming prepares the guards but does not enable live control.</p><div class="control-actions"><button id="confirm-commission" disabled>Confirm equipment and limits</button><button id="cancel-commission">Cancel</button></div>`;
  const confirm=body.querySelector('#confirm-commission');
  body.querySelectorAll('[data-check]').forEach(input=>input.onchange=()=>{confirm.disabled=Array.from(body.querySelectorAll('[data-check]')).some(x=>!x.checked);});
  confirm.onclick=()=>{const checks=Object.fromEntries(Array.from(body.querySelectorAll('[data-check]')).map(x=>[x.dataset.check,x.checked]));this.controlAction('confirm',{nonce:p.nonce,checks});};
  body.querySelector('#cancel-commission').onclick=()=>{this.commissioning=null;this.renderControl();};
 }
 async controlAction(action,data){
  if(this.controlBusy)return;this.controlBusy=true;
  this.shadowRoot.querySelectorAll('#control-body button').forEach(b=>b.disabled=true);
  this.text('control-message','Checking…');
  try{
   const id=this._panel.config.entry_id;
   const result=await this._hass.callApi('POST',`docan_deye_ems/${encodeURIComponent(id)}/control`,{action,data});
   this.commissioning=action==='preview'?result:null;
   this.text('control-message',action==='preview'?'Review the readings and limits.':action==='enable_live'?'Live control enabled.':action==='confirm'?'Equipment commissioned. Control is still inactive.':'Control state updated.');
  }catch(error){
   this.commissioning=null;
   const reason=error?.body?.error||error?.message||'Control operation failed';
   this.text('control-message',String(reason).replaceAll('_',' '));
  }finally{this.controlBusy=false;await this.refresh();this.renderControl();}
 }
 renderForecast(){
  const d=this.data,p=d.plan||{},m=d.metrics||{},h=p.horizon||[],money=d.prices?.currency||'';
  const enabled=p.mode==='forecast_shadow';
  this.shadowRoot.getElementById('forecast-section').hidden=!enabled;
  this.shadowRoot.getElementById('statistics-section').hidden=!enabled;
  if(!enabled)return;
  const first=h[0],last=h.at(-1);
  this.text('forecast-range',first?`${new Date(first.start).toLocaleString([],{timeZone:d.time_zone})} – ${new Date(last.end).toLocaleString([],{timeZone:d.time_zone})} · ${p.anticipated_intervals||0} intervals use anticipated prices. ${p.deadline_passed?'Today’s charge deadline has passed; the end-of-horizon reserve still applies.':''}`:'No feasible forecast schedule available.');
  const cards=[['Solar forecast',p.forecast_solar_kwh,'kWh'],['Home forecast',p.forecast_load_kwh,'kWh'],['Simulated import',p.estimated_import_kwh,'kWh'],['Simulated export',p.estimated_export_kwh,'kWh'],['Net grid cost',p.estimated_net_cost,money],['Wear cost',p.estimated_wear_cost,money]];
  this.shadowRoot.getElementById('forecast-summary').innerHTML=cards.map(([label,value,unit])=>`<div><small>${escape(label)}</small><b>${escape(this.format(value,2))} ${escape(unit)}</b></div>`).join('');
  const e=p.efficiency||{},load=p.load_model||{};
  this.text('learning-status',`Load: ${(load.source||'unavailable').replaceAll('_',' ')} · ${load.learned_slots||0}/${load.total_slots||0} learned slots · solar: ${(p.solar_source||'unavailable').replaceAll('_',' ')}. Charge efficiency ${this.format(e.charge,1)}% (${(e.charge_source||'entered').replaceAll('_',' ')}); discharge ${this.format(e.discharge,1)}% (${(e.discharge_source||'entered').replaceAll('_',' ')}). Battery readings: ${(d.battery_source||'unavailable').replaceAll('_',' ')}.`);
  this.text('forecast-errors',`${m.compared_intervals||0} complete forecast comparisons · load MAE ${this.format(m.load_forecast_mae_w)} W · solar MAE ${this.format(m.solar_forecast_mae_w)} W · mean absolute shadow/observed cost difference ${this.format(m.plan_cost_mae,3)} ${money}. The shadow plan was not executed; this cost difference is not forecast accuracy or proven savings.`);
  this.shadowRoot.getElementById('daily-statistics').innerHTML=(m.days||[]).slice(-7).reverse().map(r=>`<tr><td>${escape(r.date)}</td><td>${escape(this.format(r.coverage_seconds/3600,2))}</td><td>${escape(this.format(r.import_kwh,2))}</td><td>${escape(this.format(r.export_kwh,2))}</td><td>${r.unpriced_export_kwh>0?'Incomplete tariff':escape(this.format(r.observed_net_cost,2)+' '+money)}</td></tr>`).join('');
  if(this.forecastChart){
   this.forecastChart.data.datasets=[['Solar','pv_kwh','#ffd12c'],['Home','load_kwh','#ff9746'],['Grid import','grid_import_kwh','#60a5fa'],['Grid export','grid_export_kwh','#33d7a4']].map(([label,key,color])=>({label,data:h.map(r=>({x:Date.parse(r.start),y:r[key]/((Date.parse(r.end)-Date.parse(r.start))/3600000)})),borderColor:color,borderWidth:2,pointRadius:0,stepped:true}));
   this.forecastChart.data.datasets.push({label:'SoC %',data:h.flatMap(r=>[{x:Date.parse(r.start),y:r.soc_start},{x:Date.parse(r.end),y:r.soc_end}]),borderColor:'#c4a5ff',borderWidth:2,pointRadius:0,yAxisID:'soc'});
   if(first){this.forecastChart.options.scales.x.min=Date.parse(first.start);this.forecastChart.options.scales.x.max=Date.parse(last.end);}
   this.forecastChart.update('none');
  }
 }
 sendFrames(){
  if(!this.data)return;const d=this.data,states={},options={};
  for(const [key,unit] of Object.entries(UNITS)){const id=`sensor.household_${key}`;options[key]=id;states[id]={entity_id:id,state:numeric(d.values?.[key])?String(d.values[key]):'unavailable',attributes:{unit_of_measurement:unit},last_changed:d.updated_at,last_updated:d.updated_at};}
  Object.assign(options,{map_enabled:!!d.location?.enabled,latitude:d.location?.latitude,longitude:d.location?.longitude,time_zone:d.time_zone,battery_capacity:d.capacity_kwh,battery_shutdown_soc:d.reserve_soc,solar_max_power:(d.solar?.kwp||0)*1000,solar_connection:d.solar?.connection,solar_1_name:'Solar'});
  for(const frame of this.shadowRoot.querySelectorAll('iframe[data-kind]'))frame.contentWindow?.postMessage({type:'docan-deye-card-state',kind:frame.dataset.kind,options,states,language:this._hass?.language||'en'},location.origin);
 }
 onMessage(event){if(event.origin===location.origin&&event.data?.type==='docan-deye-card-ready'&&[...this.shadowRoot.querySelectorAll('iframe')].some(f=>f.contentWindow===event.source))this.sendFrames();}
 async loadHistory(){
  if(!this.charts.length||Date.now()-(this.lastHistory||0)<300000)return;
  this.lastHistory=Date.now();const from=this.data.day?.start||new Date(Date.now()-24*3600000).toISOString();
  await Promise.all(KEYS.map(async(key,i)=>{
   const id=this.data.history_entities?.[key];if(!id)return;
   try{
    const rows=await this._hass.callApi('GET',`history/period/${encodeURIComponent(from)}?filter_entity_id=${encodeURIComponent(id)}&minimal_response&no_attributes`);
    const points=(rows[0]||[]).map(r=>({x:Date.parse(r.last_updated||r.last_changed),y:r.state!==''&&Number.isFinite(Number(r.state))?Number(r.state):null})).filter(r=>Number.isFinite(r.x));
    if(this.charts[i]){this.charts[i].data.datasets[0].data=points;this.charts[i].update('none');this.shadowRoot.getElementById(`empty-${i}`).hidden=points.length>1;}
   }catch{this.text(`empty-${i}`,'History unavailable');}
  }));
 }
 async exportSettings(){
  try{const data=await this._hass.callApi('GET',`docan_deye_ems/${encodeURIComponent(this._panel.config.entry_id)}/settings`);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='docan-deye-ems-settings.private.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  catch{this.shadowRoot.getElementById('notice').hidden=false;this.text('notice','Settings export failed. Try again when Home Assistant is connected.');}
 }
}
customElements.define('docan-deye-ems-panel',DocanDeyeEMSPanel);
