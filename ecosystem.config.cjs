// PM2 config for the local arbgrid dashboard.
// `cwd` is intentionally absolute and per-machine — update if the local
// clone is moved to ~/Dev/arbgrid post-rename.
module.exports = {
  apps: [
    {
      name: 'arbgrid-dashboard',
      script: 'run_dashboard.py',
      interpreter: 'python',
      cwd: 'C:\\Users\\jtamm\\Dev\\polymarket-arb-scanner',
      env: {
        DASHBOARD_PORT: '8081',
      },
    },
  ],
};
