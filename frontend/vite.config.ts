import {defineConfig,loadEnv} from 'vite'
import react from '@vitejs/plugin-react'
import {requireBrowserSafeSupabaseKey} from './src/owner/supabasePublicConfig.ts'

export default defineConfig(({mode})=>{
  const environment=loadEnv(mode,import.meta.dirname,'')
  const publicKey=(process.env.VITE_SUPABASE_PUBLISHABLE_KEY??environment.VITE_SUPABASE_PUBLISHABLE_KEY)?.trim()
  if(publicKey)requireBrowserSafeSupabaseKey(publicKey)
  return{
    plugins:[react()],
    base:'/StockScout-Unified/',
    server:{
      // The checked-in frontend intentionally has no generated market assets.
      // Local UI work therefore reads the currently published immutable run.
      proxy:{'/StockScout-Unified/data':{target:'https://garrincha077.github.io',changeOrigin:true}},
    },
    build:{
      outDir:'dist',
      sourcemap:false,
      rollupOptions:{output:{manualChunks(id){
        const moduleId=id.replaceAll('\\','/')
        if(moduleId.includes('/node_modules/lightweight-charts/'))return'charts'
        if(moduleId.includes('/node_modules/@tanstack/react-table/'))return'table'
        if(/\/node_modules\/(react|react-dom|scheduler)\//.test(moduleId))return'react-vendor'
      }}},
    },
  }
})
