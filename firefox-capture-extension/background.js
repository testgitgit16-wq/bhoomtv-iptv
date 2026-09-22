const STREAM_RE = /\.(m3u8|mpd)(?:$|[?#])/i;
const CANONICAL_RE = /\.(m3u8|mpd)\?[^#]*\bid=/i;
const MAX_ITEMS = 2000;
const CHANNEL_TIMEOUT_MS = 18000;
const PARALLEL_CHANNELS = 2;

const state = {running:false, stage:"idle", total:0, completed:0, current:[], inventory:[]};

async function getCaptures(){const d=await browser.storage.local.get({captures:[]});return Array.isArray(d.captures)?d.captures:[];}
async function saveCapture(item){
  const a=await getCaptures();
  const key=[item.channelUrl,item.streamUrl,item.referer,item.userAgent].join("|");
  if(a.some(x=>[x.channelUrl,x.streamUrl,x.referer,x.userAgent].join("|")===key)) return;
  a.unshift(item); await browser.storage.local.set({captures:a.slice(0,MAX_ITEMS)});
}
function isBhoomLive(url){try{const u=new URL(url);return (u.hostname==="bhoomtv.org"||u.hostname==="www.bhoomtv.org")&&u.pathname.startsWith("/live/");}catch(_){return false;}}
function rankStream(url){const x=url.toLowerCase();let s=0;if(CANONICAL_RE.test(url))s+=100;if(x.includes("stream.m3u8"))s+=20;if(x.includes("segment="))s-=80;if(x.includes("token="))s-=10;if(x.includes(".mpd"))s+=10;return s;}
async function sendStatus(extra={}){await browser.storage.local.set({autoStatus:{running:state.running,stage:state.stage,total:state.total,completed:state.completed,current:state.current.slice(),inventoryCount:state.inventory.length,capturedCount:(await getCaptures()).length,...extra,updatedAt:new Date().toISOString()}});}
async function openTab(url){return browser.tabs.create({url,active:false});}
async function waitForTab(id,ms){return new Promise(resolve=>{const t=setTimeout(()=>{browser.tabs.onUpdated.removeListener(l);resolve();},ms);const l=(tabId,info)=>{if(tabId!==id||info.status!=="complete")return;clearTimeout(t);browser.tabs.onUpdated.removeListener(l);resolve();};browser.tabs.onUpdated.addListener(l);});}
async function categoryPage(url){const tab=await openTab(url);try{await waitForTab(tab.id,12000);return await browser.tabs.sendMessage(tab.id,{type:"extractCategory"})||{links:[],next:null,challenged:false};}catch(e){return{links:[],next:null,challenged:false,error:String(e)};}finally{try{await browser.tabs.remove(tab.id);}catch(_) {}}}
async function crawlCategories(){
  const cats=[{group:"Tamil TV",url:"https://bhoomtv.org/channel/tamil/"},{group:"Tamil Local TV",url:"https://bhoomtv.org/channel/tamil-local-tv/"}];
  const byUrl=new Map();
  for(const cat of cats){
    let next=cat.url; const visited=new Set(); let guard=0;
    while(next&&!visited.has(next)&&guard++<500){
      visited.add(next); state.stage="inventory"; await sendStatus({category:cat.group,pageUrl:next});
      const r=await categoryPage(next);
      if(r.challenged&&r.links.length===0){await sendStatus({error:"BhoomTV browser page returned a Cloudflare challenge."});break;}
      for(const row of r.links||[]) if(!byUrl.has(row.url)) byUrl.set(row.url,{...row,group:cat.group});
      if(!r.links||r.links.length===0) break;
      next=r.next||null;
    }
  }
  state.inventory=Array.from(byUrl.values());state.total=state.inventory.length;
  await browser.storage.local.set({inventory:state.inventory});await sendStatus();
}
async function scanChannel(ch){
  const tab=await openTab(ch.url);const started=Date.now();
  try{
    await waitForTab(tab.id,10000);
    try{await browser.tabs.sendMessage(tab.id,{type:"playChannel"});}catch(_){}
    while(Date.now()-started<CHANNEL_TIMEOUT_MS){
      const rows=(await getCaptures()).filter(x=>x.channelUrl===ch.url);
      if(rows.some(x=>rankStream(x.streamUrl)>=100)) break;
      await new Promise(r=>setTimeout(r,1000));
    }
  }finally{try{await browser.tabs.remove(tab.id);}catch(_){}}
}
async function runChannelScan(){
  state.stage="streams";const q=state.inventory.slice();let i=0;
  async function worker(){
    while(i<q.length&&state.running){
      const ch=q[i++];state.current.push(ch.title);state.current=state.current.slice(-PARALLEL_CHANNELS);
      await sendStatus();await scanChannel(ch);
      state.current=state.current.filter(x=>x!==ch.title);state.completed++;await sendStatus();
    }
  }
  await Promise.all(Array.from({length:PARALLEL_CHANNELS},()=>worker()));
}
function canonicalM3U(rows){
  const best=new Map();
  for(const row of rows){if(!isBhoomLive(row.channelUrl)||!STREAM_RE.test(row.streamUrl))continue;const cur=best.get(row.channelUrl);if(!cur||rankStream(row.streamUrl)>rankStream(cur.streamUrl))best.set(row.channelUrl,row);}
  const out=["#EXTM3U"];
  for(const row of Array.from(best.values()).sort((a,b)=>String(a.channelName).localeCompare(String(b.channelName)))){
    const title=String(row.channelName||"BhoomTV").replace(/"/g,"&quot;"), ref=String(row.referer||row.channelUrl||""), ua=String(row.userAgent||"Mozilla/5.0"), url=String(row.streamUrl||"");
    out.push('#EXTINF:-1 group-title="Tamil",'+title);out.push("#EXTVLCOPT:http-referrer="+ref);out.push("#EXTVLCOPT:http-user-agent="+ua);out.push(url+"|Referer="+ref+"&User-Agent="+ua);out.push("");
  }
  return out.join("\n").trim()+"\n";
}
async function startAutoScan(){
  if(state.running)return{ok:false,error:"Already running"};
  state.running=true;state.stage="starting";state.completed=0;state.total=0;state.inventory=[];state.current=[];
  await browser.storage.local.set({captures:[]});await sendStatus();
  try{await crawlCategories();if(state.inventory.length)await runChannelScan();state.stage="complete";}catch(e){state.stage="error";await sendStatus({error:String(e)});}
  finally{state.running=false;state.current=[];await sendStatus();}
  return{ok:true};
}
browser.webRequest.onBeforeSendHeaders.addListener(async details=>{
  if(!STREAM_RE.test(details.url)||details.tabId<0)return;
  let tab;try{tab=await browser.tabs.get(details.tabId);}catch(_){return;}
  if(!isBhoomLive(tab?.url||""))return;
  const hs=details.requestHeaders||[];
  const get=n=>{const h=hs.find(x=>String(x.name||"").toLowerCase()===n.toLowerCase());return h?String(h.value||""):"";};
  await saveCapture({channelName:tab.title||tab.url,channelUrl:tab.url,streamUrl:details.url,referer:get("Referer")||"",userAgent:get("User-Agent")||"",capturedAt:new Date().toISOString()});
  await sendStatus({lastCapture:details.url});
},{urls:["<all_urls>"]},["requestHeaders","extraHeaders"]);
browser.runtime.onMessage.addListener(async m=>{
  if(!m||typeof m.type!=="string")return null;
  if(m.type==="startAutoScan")return startAutoScan();
  if(m.type==="stopAutoScan"){state.running=false;state.stage="stopping";await sendStatus();return{ok:true};}
  if(m.type==="getStatus"){const d=await browser.storage.local.get({autoStatus:null});return d.autoStatus||{running:false,stage:"idle",total:0,completed:0,current:[],inventoryCount:0,capturedCount:(await getCaptures()).length};}
  if(m.type==="getCaptures")return{captures:await getCaptures()};
  if(m.type==="getM3U")return{m3u:canonicalM3U(await getCaptures())};
  if(m.type==="clearCaptures"){await browser.storage.local.set({captures:[]});return{ok:true};}
  if(m.type==="downloadM3U"){const b=new Blob([canonicalM3U(await getCaptures())],{type:"audio/x-mpegurl"}),u=URL.createObjectURL(b);await browser.downloads.download({url:u,filename:"bhoomtv-captured.m3u",saveAs:true});setTimeout(()=>URL.revokeObjectURL(u),5000);return{ok:true};}
  if(m.type==="downloadJSON"){const b=new Blob([JSON.stringify(await getCaptures(),null,2)],{type:"application/json"}),u=URL.createObjectURL(b);await browser.downloads.download({url:u,filename:"bhoomtv-captured.json",saveAs:true});setTimeout(()=>URL.revokeObjectURL(u),5000);return{ok:true};}
  return null;
});
sendStatus();