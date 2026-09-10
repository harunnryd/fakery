# ADR 0011: Guest profiles belong to individual runs

Live testing after a worker rollout failed with profile-busy before Job creation.
The runner acquired one tenant-wide guest profile lease even though the browser
already creates a separate guest directory per run and never restores guest
profiles from blob storage. A terminated supervisor can leave that shared lease
alive for one hour.

Guest runs do not acquire a shared profile lease. Their database capacity
reservation and run ownership protect scheduling; guest join pacing remains
independent. Signed profiles retain their exclusive lease. This change does not
claim signed identity renewal or restart-safe pacing is complete.
