# Setting up Google Calendar for Nomad Life

How to create the Google OAuth client behind **Settings > Calendar Integration**, where people connect a Google Calendar and choose one of their calendars. For now the app only asks Google for the list of calendars (`calendar.calendarlist.readonly`); what the chosen calendar is used for comes later and will ask for its own permission.

What the app needs at the end: two values for `config.env` on the server.

```
GOOGLE_CLIENT_ID=        # step 4
GOOGLE_CLIENT_SECRET=    # step 4
```

Until both are set, the section says "Calendar Integration is not set up on this server yet". Restart the service after setting them.

Menu names are Google Cloud's at the time of writing; the search box at the top of the console finds any page by name.

## 1. Create a project

1. Open https://console.cloud.google.com with the Google account that will own the app (better a company account than a personal one).
2. Create a project, for example **Nomad Life**, and select it.

## 2. Enable the Google Calendar API

**APIs & Services -> Library**, search **Google Calendar API**, press **Enable**.

## 3. Consent screen (Google Auth Platform)

1. **Branding:** app name **Nomad Life**, support email, the logo (`static/img/logo-mark.png`), home page `https://nomadlife.pro`, privacy policy `https://nomadlife.pro/privacy`, terms `https://nomadlife.pro/terms`, and the authorized domain `nomadlife.pro`.
2. **Audience:** External. While the app is in **Testing**, only the test users listed here can connect (up to 100), and their connection expires after 7 days: add your own Google address to try it.
3. **Data access:** add the scope `https://www.googleapis.com/auth/calendar.calendarlist.readonly` ("See the list of Google calendars you're subscribed to").

## 4. Create the OAuth client

**Clients -> Create client**:

- **Application type:** Web application. **Name:** Nomad Life.
- **Authorized redirect URIs:** `https://nomadlife.pro/settings/calendar/google/callback` (it is `APP_BASE_URL` followed by `/settings/calendar/google/callback`). For a local test add `http://localhost:5050/settings/calendar/google/callback` too, with `APP_PORT` as the port.
- No JavaScript origins are needed: the browser never talks to Google's API.

Copy the **Client ID** and **Client secret** into `config.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, then restart the app.

## 5. Try it

1. Sign in, open **Settings > Calendar Integration** (or the gear next to the dashboard's Calendar title) and press **Connect Google Calendar**.
2. Sign in with a test user and press **Continue**. Back in Settings, the calendars you can edit are listed, your main one first: choose one and press **Save calendar**.
3. **Disconnect** removes the connection and Nomad Life's access: it disappears from https://myaccount.google.com/connections.

## 6. Going public

Calendar scopes are **sensitive**: before people outside the test users can connect, Google must verify the app (**Audience -> Publish app**, then the verification Google asks for: the privacy policy, a short video of the flow, and why the scope is needed). Until then, everyone else sees "Access blocked".

## What is stored

The `calendar_links` table holds, per account, the Google address, the refresh token, the list of calendars it may edit (names, colors) and the one chosen. Disconnecting or deleting the account deletes the row and revokes the token at Google. The database file stays readable by the app's user only (chmod 600).
