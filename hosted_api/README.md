# autoship hosted API

This is the private deploy service behind `--deploy autoship`.

The open-source CLI does not need VPS credentials. It:

1. Logs in once with an invite code to get a stored deploy token
2. Builds the app locally with `codex` or `claude`
3. Packages the output directory into a tarball
4. Uploads the bundle to this API with a bearer token

The private API then:

1. Verifies the token
2. Runs [`deploy_release.sh`](/Users/usman/Documents/Vibed/redbee/autoship/deploy_release.sh)
3. Provisions runtime env vars from `autoship.plan.json`
4. Starts the container, wires nginx, requests TLS, returns the app URL

## Files

- [`server.py`](/Users/usman/Documents/Vibed/redbee/autoship/hosted_api/server.py): stdlib HTTP deploy API
- [`autoship-api.service`](/Users/usman/Documents/Vibed/redbee/autoship/hosted_api/autoship-api.service): systemd unit example
- [`deploy_release.sh`](/Users/usman/Documents/Vibed/redbee/autoship/deploy_release.sh): shared deploy script used by both SSH operator mode and hosted API mode
- [`issue_invite.py`](/Users/usman/Documents/Vibed/redbee/autoship/hosted_api/issue_invite.py): one-time invite code generator

## Install on the VPS

Copy these files to `/opt/autoship/api`:

- `server.py`
- `deploy_release.sh`
- `autoship-api.service`

Create `/opt/autoship/api/api.env` with:

```bash
AUTOSHIP_DEPLOY_TOKEN=replace-with-long-random-token
AUTOSHIP_EMAIL=you@example.com
```

Optional file-backed auth state:

```bash
touch /opt/autoship/api/tokens.json
touch /opt/autoship/api/invites.json
```

Install and start:

```bash
sudo cp /opt/autoship/api/autoship-api.service /etc/systemd/system/autoship-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now autoship-api
```

Expose it through nginx at `api.autoship.fun`, proxying to `127.0.0.1:9100`.

## Invite flow

Issue an invite code on the server:

```bash
python3 /opt/autoship/api/issue_invite.py /opt/autoship/api/invites.json "beta-user"
```

User claims it locally:

```bash
python3 autoship.py login --code SHIP-ABC123
```

## OSS client usage

```bash
python3 autoship.py login --code SHIP-ABC123
python3 autoship.py spec.md -e codex --deploy autoship
```

The client stores the claimed token at `~/.config/autoship/auth.json` and uses `https://api.autoship.fun/deploy` by default.
