let timer=null;const $=id=>document.getElementById(id);
async function status(){const r=await browser.runtime.sendMessage({type:"getStatus"});$("status").textContent=[(r.stage||"idle").toUpperCase(),"channels "+(r.completed||0)+"/"+(r.total||0),"captured "+(r.capturedCount||0)].join(" | ");const x=await browser.runtime.sendMessage({type:"getCaptures"});const rows=Array.isArray(x.captures)?x.captures:[];$("items").innerHTML=rows.slice(0,15).map(q=>"<div class='card'><b>"+String(q.channelName||"BhoomTV").replace(/</g,"&lt;")+"</b><div>"+String(q.streamUrl||"").replace(/</g,"&lt;")+"</div><div>Referer: "+String(q.referer||"").replace(/</g,"&lt;")+"</div></div>").join("");if(!r.running&&timer){clearInterval(timer);timer=null;}}
$("start").onclick=async()=>{await browser.runtime.sendMessage({type:"startAutoScan"});if(!timer)timer=setInterval(status,1500);status();};
$("stop").onclick=async()=>{await browser.runtime.sendMessage({type:"stopAutoScan"});status();};
$("download").onclick=async()=>{await browser.runtime.sendMessage({type:"downloadM3U"});};
$("json").onclick=async()=>{await browser.runtime.sendMessage({type:"downloadJSON"});};
$("clear").onclick=async()=>{await browser.runtime.sendMessage({type:"clearCaptures"});status();};
status();