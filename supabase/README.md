# Movie World cloud account

Supabase holds the signed-in user's profile, favourites, and watch progress. Catalogue, Telegram, and TV playback stay on each device / the LAN server.

## Project

- Hosted project: `Movie World` (`mwlwlmywnnreihbeatgw`), region `ap-southeast-2`
- API URL: `https://mwlwlmywnnreihbeatgw.supabase.co`
- Schema: [migrations/20260307000000_cloud_account_sync.sql](migrations/20260307000000_cloud_account_sync.sql)

## Client config

Web: copy [../web/cloud-config.example.js](../web/cloud-config.example.js) to `web/cloud-config.js` and set the URL plus the publishable key from the dashboard (Project Settings → API).

Android: add to `local.properties`:

```
supabase.url=https://mwlwlmywnnreihbeatgw.supabase.co
supabase.publishableKey=sb_publishable_…
```

Never commit the service role / secret key.
