# Deploy Khushi on Railway

One service, one replica, one volume. Allow about 30 minutes the first time.

## 1. Telegram

1. In Telegram, open **@BotFather** → `/newbot` → name it e.g. *Khushi* → copy the token.
2. Open your new bot and send `/start` (it won't answer yet).

## 2. Claude login

On your PC: `claude setup-token` → copy the whole token (it's ~108 characters, printed over **two** lines).
If your org blocks subscription access, use an API key instead (`LLM_AUTH=api_key`, `ANTHROPIC_API_KEY`).

## 3. Railway service

1. **New Project → Deploy from GitHub repo → `infosmitkushwaha-hub/khushi`**.
2. **Volume:** service → *Settings → Volumes* → mount at `/app/data`.
3. **Domain for the login viewer:** *Settings → Networking → Generate Domain*, **target port 6080**. Copy the
   `https://…up.railway.app` address.
4. **Variables** (*Variables → Raw Editor*, pasting replaces all, so paste the full set):

   ```
   LLM_AUTH=oauth
   CLAUDE_CODE_OAUTH_TOKEN=<from step 2>
   TELEGRAM_BOT_TOKEN=<from step 1>
   TELEGRAM_CHAT_ID=<fill after step 5>
   APPROVER_IDS=<fill after step 5>
   LOGIN_VIEWER_URL=<the domain from 3.3>
   LINKEDIN_CLIENT_ID=<your LinkedIn app>
   LINKEDIN_CLIENT_SECRET=<your LinkedIn app>
   DRY_RUN=true
   ```
5. Deploy. Send `/myid` to the bot: put **chat id** in `TELEGRAM_CHAT_ID` and **user id** in `APPROVER_IDS`,
   then redeploy. Khushi says "Khushi started · 🧪 DRY RUN".

## 4. Logins (once)

- **`/linkedin_login`**: Khushi sends a private link. Open it and you'll see a browser on the server. Log in to
  your LinkedIn and pass any security check, then close the page. She confirms in Telegram. The session lives on the
  volume; redo this only when she asks.
- **`/comment_auth`**: open the link, tap Allow, and paste back the `localhost` address. This lets her comment
  (about every 60 days).

> **Account safety:** the LinkedIn session is created on Railway's IP (moving a session between IPs triggers
> LinkedIn checks). If LinkedIn keeps asking for verification, add a sticky residential proxy
> (`PROXY_SERVER`, `PROXY_USERNAME`, `PROXY_PASSWORD`) and run `/linkedin_login` again.

## 5. First day

1. `/scan`: cards arrive with `[DRY RUN]`, and nothing is sent to LinkedIn.
2. Check the cards make sense. Edit `knowledge/targets.md` if not and push.
3. When you're happy: set `DRY_RUN=false` → redeploy. From then on, strong fits get a comment by
   themselves; connect and DMs still wait for your tap.

## Gotchas

- **Keep 1 replica.** Two copies fight over Telegram polling and the LinkedIn browser profile.
- **Railway "Active" isn't the same as healthy.** If the bot is silent, read the deploy logs.
- **Raw Editor paste replaces every variable.** To change one, edit just that one.
- **Slow pushes** from a flaky network: use `git -c http.version=HTTP/1.1 push`.
