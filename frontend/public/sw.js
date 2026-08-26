const CACHE='stockscout-unified-shell-v1'
const APP_ROOT=self.registration.scope
const SHELL=[APP_ROOT,'404.html','manifest.webmanifest','icons/stockscout.svg'].map(path=>new URL(path,APP_ROOT).toString())
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting())))
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())))
self.addEventListener('message',event=>{
  if(event.data?.type!=='CACHE_SCAN'||!Array.isArray(event.data.urls))return
  const urls=event.data.urls.filter(value=>{
    try{
      const url=new URL(value,self.location.origin)
      return url.origin===self.location.origin&&(url.pathname.endsWith('/manifest.json')||url.pathname.includes('/data/modes/')&&url.pathname.includes('/runs/'))
    }catch{return false}
  })
  event.waitUntil(caches.open(CACHE).then(async cache=>{
    for(const url of urls){
      const response=await fetch(url)
      if(response.ok)await cache.put(url,response)
    }
  }))
})
self.addEventListener('fetch',event=>{
  const request=event.request
  if(request.method!=='GET')return
  const url=new URL(request.url)
  // Authenticated storage and API responses must never enter a public cache,
  // even if a future deployment proxies Supabase through the app origin.
  const isPrivate=url.hostname.endsWith('.supabase.co')||url.pathname.includes('/storage/v1/')||url.pathname.includes('/auth/v1/')||request.headers.has('authorization')
  if(url.origin!==self.location.origin||isPrivate)return
  const isManifest=url.pathname.endsWith('/manifest.json')&&url.pathname.includes('/data/')
  const isImmutable=url.pathname.includes('/data/modes/')&&url.pathname.includes('/runs/')||url.searchParams.has('v')
  if(isManifest){
    event.respondWith(fetch(request).then(response=>{const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(request,copy));return response}).catch(()=>caches.match(request)))
    return
  }
  if(isImmutable){
    event.respondWith(caches.match(request).then(cached=>cached||fetch(request).then(response=>{if(response.ok)caches.open(CACHE).then(cache=>cache.put(request,response.clone()));return response})))
    return
  }
  if(['script','style','font','image'].includes(request.destination)){
    event.respondWith(caches.match(request).then(cached=>cached||fetch(request).then(response=>{if(response.ok)caches.open(CACHE).then(cache=>cache.put(request,response.clone()));return response})))
    return
  }
  if(request.mode==='navigate'){
    const tickerMatch=url.pathname.match(/\/ticker\/([^/]+)\/?$/i)
    if(tickerMatch){
      const target=new URL(APP_ROOT)
      target.search=url.search
      let ticker=tickerMatch[1]
      try{ticker=decodeURIComponent(ticker)}catch{}
      target.searchParams.set('ticker',ticker)
      event.respondWith(Promise.resolve(Response.redirect(target.toString(),302)))
      return
    }
    event.respondWith(fetch(request).then(response=>{
      if(response.ok&&url.pathname===new URL(APP_ROOT).pathname){
        const copy=response.clone()
        caches.open(CACHE).then(cache=>cache.put(APP_ROOT,copy))
      }
      return response
    }).catch(()=>caches.match(APP_ROOT)))
  }
})
