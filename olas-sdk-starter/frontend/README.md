# Pett Agent Frontend

React frontend for the Pett Agent, managed directly by Python.

## Setup

The Python backend automatically handles:

- Installing npm dependencies on first boot
- Starting the React dev server (`yarn start` or `npm start`)
- Proxying requests from the Python server

## Configuration

### Environment Variables

The frontend requires a `.env` file with the following required variable:

- `REACT_APP_PRIVY_APP_ID` - Your Privy App ID (required for authentication)

### Setup

1. **Automatic Setup**: The build process will automatically create a `.env` file from `.env.example` if it doesn't exist.

2. **Manual Setup**: Copy `.env.example` to `.env` and update the values:

   ```bash
   cp .env.example .env
   ```

3. **Set your Privy App ID** in `.env`:
   ```
   REACT_APP_PRIVY_APP_ID=your-privy-app-id
   ```

### Environment Checks

The project includes automated environment variable checks:

- **Before build/start**: The `check-env.js` script automatically runs via npm `prestart` and `prebuild` hooks
- **Makefile**: The `build-frontend` target includes an environment check
- **Manual check**: Run `npm run check-env` to verify your `.env` file

If `.env` is missing or has invalid values, the check will:

- Create `.env` from `.env.example` if missing
- Report missing or empty required variables
- Exit with an error to prevent builds with invalid configuration

**Note:** `GENERATE_SOURCEMAP=false` suppresses source map warnings from WalletConnect and other dependencies. Python automatically sets this when running the dev server.

## Styling

This project uses:

- **Tailwind CSS** for utility-first styling
- **SCSS** for custom component styles
- Custom fonts: Retro Pixel and Satoshi

Tailwind is configured in `tailwind.config.js` and integrated via PostCSS.

## How It Works

1. Python starts the React dev server as a subprocess
2. React runs on port 3000
3. Python proxies `/login` and other routes to React
4. React sends Privy tokens to Python via `/api/login`
5. Python updates the WebSocket connection with the token

## Routes

- `/login` - Login page with Privy authentication
- `/dashboard` - Dashboard showing pet status and actions
- `/api/login` - API endpoint for authentication (Python backend)
- `/api/status` - API endpoint for status updates (Python backend)

## Manual Development

If you want to run React separately:

```bash
npm install  # or yarn install
npm start    # or yarn start
```

The app will open on http://localhost:3000
