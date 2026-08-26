import type {LegacyConfirmationStatus} from './deepvue/runtime'
import {useStockScoutData} from './data/StockScoutDataProvider'

type BadgeEntry={status:LegacyConfirmationStatus;available:boolean;reasons?:string[]}

export default function LegacyConfirmationBadge(){
  const{core,selectedTicker:ticker}=useStockScoutData()
  const row=core?.universe.find(stock=>stock.ticker===ticker)
  const entry:BadgeEntry|undefined=row?.legacyConfirmationStatus?{
    status:row.legacyConfirmationStatus,
    available:true,
    reasons:row.legacyConfirmationReasons,
  }:undefined
  if(!ticker||!entry)return null
  const reasons=(entry.reasons||[]).join(' · ')
  return <div className={`lc-shadow-badge lc-${entry.status.toLowerCase()}`} title={reasons||'Frozen LEGACY shadow confirmation'} aria-label={`LEGACY confirmation ${entry.status} for ${ticker}`}>
    <span>LEGACY</span><b>{entry.status}</b><small>{ticker}</small>
  </div>
}
