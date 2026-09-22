function clean(v){return String(v||"").replace(/\s+/g," ").trim();}
function canonical(v){try{return new URL(v,location.href).toString().split("#")[0];}catch(_){return "";}}
function isChallenge(){const t=(document.title+" "+(document.body?.innerText||"")).toLowerCase();return["just a moment","verify you are human","attention required","cf-chl-","challenge-platform","cloudflare ray id"].some(x=>t.includes(x));}
function extractCategory(){
  const links=[],seen=new Set();
  for(const a of document.querySelectorAll("a[href]")){
    const href=canonical(a.href);try{const u=new URL(href);
      if(!["bhoomtv.org","www.bhoomtv.org"].includes(u.hostname)||!u.pathname.includes("/live/")||seen.has(href))continue;
      const img=a.querySelector("img");
      const title=clean(a.innerText)||clean(img?.alt)||(u.pathname.split("/").filter(Boolean).pop()||"channel").replace(/[-_]+/g," ");
      links.push({title,url:href,logo:img?.src||""});seen.add(href);
    }catch(_){}}
  let next="";
  for(const a of document.querySelectorAll("a[href]")){
    const tx=clean(a.innerText).toLowerCase(),rel=Array.from(a.rel||[]).join(" ").toLowerCase(),cl=String(a.className||"").toLowerCase(),href=canonical(a.href);
    if(["next","next page","›","»"].includes(tx)||rel.includes("next")||cl.includes("next")){next=href;break;}
  }
  if(!next&&links.length){const m=location.pathname.match(/\/page\/(\d+)\/?$/);const page=m?Number(m[1]):1;next=canonical(m?location.pathname.replace(/\/page\/\d+\/?$/,"")+"/page/"+(page+1)+"/":location.pathname.replace(/\/$/,"")+"/page/"+(page+1)+"/");}
  return{links,next:next||null,title:document.title,challenged:isChallenge()};
}
async function playChannel(){
  const videos=Array.from(document.querySelectorAll("video"));
  for(const v of videos){try{v.muted=true;v.setAttribute("muted","");await v.play();}catch(_){}}
  const selectors=["button[aria-label*='play' i]","[role='button'][aria-label*='play' i]",".vjs-big-play-button",".plyr__control--overlaid",".jw-display-icon-container",".jw-icon-playback","button[class*='play' i]"];
  for(const s of selectors)for(const el of document.querySelectorAll(s)){try{el.click();}catch(_){}}
  return{ok:true,videos:videos.length};
}
browser.runtime.onMessage.addListener(async m=>{if(m?.type==="extractCategory")return extractCategory();if(m?.type==="playChannel")return playChannel();return null;});
