import {defineConfig,devices} from '@playwright/test'

export default defineConfig({
  testDir:'./e2e',
  timeout:30_000,
  expect:{timeout:5_000},
  use:{
    baseURL:'http://127.0.0.1:4173',
    channel:process.env.PW_CHANNEL||undefined,
    trace:'retain-on-failure',
  },
  projects:[
    {
      name:'mobile-pixel-5',
      use:{...devices['Pixel 5']},
    },
    {
      name:'desktop-chrome',
      use:{...devices['Desktop Chrome'],viewport:{width:1440,height:1000}},
    },
  ],
  webServer:{
    command:'npm run preview -- --host 127.0.0.1 --port 4173',
    url:'http://127.0.0.1:4173',
    reuseExistingServer:false,
    timeout:30_000,
  },
})