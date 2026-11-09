const path = require('path');
module.exports = {
  testDir: __dirname,
  testMatch: 'ui.spec.cjs',
  outputDir: path.resolve(__dirname, '../.local/tizen-test-results'),
  workers: 1,
  use: {
    viewport: {width:1920,height:1080},
    launchOptions: {executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe', args:['--no-proxy-server']}
  }
};
