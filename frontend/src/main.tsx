import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './deepvue/legacyConfirmationUi'
import './deepvue/phase5Cohorts'
import App from './App'
import {StockScoutDataProvider} from './data/StockScoutDataProvider'
import {OwnerDataProvider} from './owner/OwnerDataProvider'
import {ModeProvider} from './modes/ModeProvider'
import './styles.css'
import './terminal.css'
import './datafirst.css'
import './deepvue.css'
import './grid-watchlist.css'
import './mobile-tradingview.css'
import './fundamental-evidence.css'
import './mobile-layer-fix.css'
import './mobile-grid-scroll.css'
import './stockscout-eod.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ModeProvider><OwnerDataProvider><StockScoutDataProvider><App /></StockScoutDataProvider></OwnerDataProvider></ModeProvider>
  </StrictMode>,
)

if('serviceWorker'in navigator&&import.meta.env.PROD&&!navigator.webdriver){
  window.addEventListener('load',()=>{
    const tickerIndex=location.pathname.toLowerCase().indexOf('/ticker/')
    const appRoot=tickerIndex>=0?`${location.pathname.slice(0,tickerIndex)}/`:location.pathname.endsWith('/')?location.pathname:location.pathname.replace(/[^/]+$/,'')
    navigator.serviceWorker.register(new URL(`${appRoot}sw.js`,location.origin),{scope:appRoot}).catch(()=>undefined)
  })
}
