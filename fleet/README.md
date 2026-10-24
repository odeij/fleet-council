# fleet: the `rig` CLI, scheduler and experience loop

    rig help              all commands
    rig hosts             health of every host
    rig run <host> <job> -d <dir> -- <cmd>
    rig wait <host:job ...>   ·   rig collect <host> <job>
    rig dispatch MANIFEST [--dry-run]

Layout: bin/rig (CLI) · agent/ (pushed to each host's ~/rig-runs/.bin by `rig setup`) · lib/ (dispatch.py scheduler,
learn.py, sample.py telemetry) · hosts.conf · requirements.txt (pins for ~/env on every host) · jobs/ (smoke test,
dispatch example) · experience/ (what the fleet has learned) · results/<host>/<job>/ (collected outputs).
Setup: ../docs/SETUP.md
