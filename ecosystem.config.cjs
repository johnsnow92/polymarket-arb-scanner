module.exports = {
  apps: [
    {
      name: 'polymarket-dashboard',
      script: 'run_dashboard.py',
      interpreter: 'python',
      cwd: 'C:\\Users\\jtamm\\Dev\\polymarket-arb-scanner',
      env: {
        DASHBOARD_PORT: '8081',
      },
    },
  ],
};
