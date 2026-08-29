/** Owner-only rollout switch; a missing flag keeps the feature available. */
const environment=(import.meta as ImportMeta&{env?:Record<string,string|undefined>}).env

export function indicatorAlertsEnabled(isOwner:boolean):boolean{
  return isOwner&&environment?.VITE_OWNER_INDICATOR_ALERTS!=='false'
}
