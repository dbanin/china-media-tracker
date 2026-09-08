# Self-hosted runner for the blocked outlets

Why: about fifty outlets in sources/outlets.yaml answer HTTP 403 or time out
when polled from GitHub's hosted runners, while their feeds work from an
ordinary network. They carry `collector: self_hosted` and are handled only by
the `collect self-hosted` workflow, which needs a runner on a normal network,
such as the owner's Mac.

One-time setup, done by the owner because it needs a registration token:

1. Open the repository on GitHub: Settings, Actions, Runners, New self-hosted
   runner. Choose macOS and the architecture of the machine (arm64 for Apple
   silicon).
2. In a terminal, run the Download and Configure commands the page shows.
   Accept the defaults for runner group, name and labels; the workflow only
   needs the `self-hosted` label. Choose the work folder default.
3. Install it as a service so it starts at login and survives reboots:

       cd ~/actions-runner
       ./svc.sh install
       ./svc.sh start

4. Check: Settings, Actions, Runners shows the machine as Idle. The next
   `collect self-hosted` run (hourly at :47 UTC, or Run workflow) then
   executes on it, and the site's outlet table shows those feeds as healthy.

Notes:

- The runner needs Python 3.11 available; actions/setup-python installs it
  into the runner's tool cache on first use.
- The job shares the database through the Actions cache, so nothing is stored
  permanently on the machine beyond the runner's own work folder.
- If the Mac is asleep, the job waits in the queue and is cancelled by its
  40-minute timeout. Nothing else is affected.
- To pause it, stop the service: `./svc.sh stop`. To remove it, `./svc.sh
  uninstall` and then `./config.sh remove`.
