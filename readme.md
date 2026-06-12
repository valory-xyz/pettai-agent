# Pett Agent - Olas SDK

Autonomous agent for managing virtual pets on Pett.ai, built with Olas SDK compliance.

## Overview

The Pett Agent is an autonomous agent service that manages virtual pets through the Pett.ai platform. It connects via WebSocket, monitors pet stats, and performs actions to keep pets healthy and happy.

## Prerequisites

- Docker installed and running
- Docker Hub account (for pushing images)
- Ethereum private key for the agent wallet
- Required API tokens and credentials (see Configuration section)

## Quick Start

### 1. Build the Docker Image

Navigate to the `olas-sdk-starter` directory and build the Docker image:

```bash
cd olas-sdk-starter
docker build -t pettaidev/oar-pett_agent:bafybeifb4opp4ej2f7msst54exbhh5egv5w2nmipgb5ul5t3a4jwtfec6m .
```

### 2. Push to Docker Hub

```bash
docker push pettaidev/oar-pett_agent:bafybeifb4opp4ej2f7msst54exbhh5egv5w2nmipgb5ul5t3a4jwtfec6m
```

### 3. Run the Agent

#### Option A: Using Docker with Environment Variables

```bash
docker run -d \
  --name pett_agent \
  -p 8716:8716 \
  -e ETH_PRIVATE_KEY="your_ethereum_private_key_here" \
  -e PRIVY_TOKEN="your_privy_token" \
  -e TELEGRAM_BOT_TOKEN="your_telegram_bot_token" \
  -e WEBSOCKET_URL="wss://ws.pett.ai" \
  -e OPENAI_API_KEY="your_openai_api_key" \
  -e SAFE_CONTRACT_ADDRESSES="your_safe_addresses" \
  -v $(pwd)/data:/data \
  pettaidev/oar-pett_agent:bafybeifb4opp4ej2f7msst54exbhh5egv5w2nmipgb5ul5t3a4jwtfec6m
```

#### Option B: Using Docker with Private Key File

Create a file `ethereum_private_key.txt` in the `olas-sdk-starter` directory with your private key:

```bash
# Create the private key file
echo "your_ethereum_private_key_here" > olas-sdk-starter/ethereum_private_key.txt

# Run the container
docker run -d \
  --name pett_agent \
  -p 8716:8716 \
  -v $(pwd)/olas-sdk-starter/ethereum_private_key.txt:/app/ethereum_private_key.txt \
  -v $(pwd)/olas-sdk-starter/data:/data \
  -e PRIVY_TOKEN="your_privy_token" \
  -e TELEGRAM_BOT_TOKEN="your_telegram_bot_token" \
  -e WEBSOCKET_URL="wss://ws.pett.ai" \
  -e OPENAI_API_KEY="your_openai_api_key" \
  -e SAFE_CONTRACT_ADDRESSES="your_safe_addresses" \
  pettaidev/oar-pett_agent:bafybeifb4opp4ej2f7msst54exbhh5egv5w2nmipgb5ul5t3a4jwtfec6m
```

#### Option C: Using Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  pett_agent:
    image: pettaidev/oar-pett_agent:bafybeifb4opp4ej2f7msst54exbhh5egv5w2nmipgb5ul5t3a4jwtfec6m
    container_name: pett_agent
    ports:
      - '8716:8716'
    environment:
      - ETH_PRIVATE_KEY=${ETH_PRIVATE_KEY}
      - PRIVY_TOKEN=${PRIVY_TOKEN}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - WEBSOCKET_URL=${WEBSOCKET_URL:-wss://ws.pett.ai}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - SAFE_CONTRACT_ADDRESSES=${SAFE_CONTRACT_ADDRESSES}
    volumes:
      - ./data:/data
      - ./ethereum_private_key.txt:/app/ethereum_private_key.txt
    restart: unless-stopped
```

Then run:

```bash
docker-compose up -d
```

## Configuration

### Required Environment Variables

| Variable                  | Description                                     | Required                            |
| ------------------------- | ----------------------------------------------- | ----------------------------------- |
| `ETH_PRIVATE_KEY`         | Ethereum private key for the agent wallet       | Yes                                 |
| `PRIVY_TOKEN`             | Privy authentication token                      | Yes                                 |
| `TELEGRAM_BOT_TOKEN`      | Telegram bot token (if using Telegram features) | Optional                            |
| `WEBSOCKET_URL`           | WebSocket URL for Pett.ai connection            | No (defaults to `wss://ws.pett.ai`) |
| `OPENAI_API_KEY`          | OpenAI API key for AI features                  | Optional                            |
| `SAFE_CONTRACT_ADDRESSES` | Safe contract addresses                         | Optional                            |

### Private Key Configuration

The agent supports multiple methods for providing the Ethereum private key:

1. **Environment Variable**: `ETH_PRIVATE_KEY` or `CONNECTION_CONFIGS_CONFIG_ETH_PRIVATE_KEY`
2. **File**: `./ethereum_private_key.txt` (in the container's working directory)
3. **File**: `../agent_key/ethereum_private_key.txt` (relative to working directory)

The private key can be:

- Plain text hex string (with or without `0x` prefix)
- Encrypted keystore (requires `--password` flag when running)

## Accessing the Agent

Once the agent is running, you can access:

- **Health Check**: http://localhost:8716/healthcheck
- **Agent UI**: http://localhost:8716/

The agent exposes port `8716` for health checks and the web UI, as required by the Olas SDK.

## Monitoring

### View Logs

```bash
# View logs
docker logs pett_agent

# Follow logs in real-time
docker logs -f pett_agent
```

### Check Container Status

```bash
docker ps | grep pett_agent
```

### Stop the Agent

```bash
docker stop pett_agent
```

### Remove the Container

```bash
docker rm pett_agent
```

## Development

### Running Locally (Without Docker)

1. Install Python 3.11+ and dependencies:

```bash
cd olas-sdk-starter
pip install -r requirements.txt
```

2. Set up environment variables (create a `.env` file or export them):

```bash
export ETH_PRIVATE_KEY="your_key"
export PRIVY_TOKEN="your_token"
# ... other variables
```

3. Run the agent:

```bash
python run.py
```

### Building Frontend

The frontend is built during the Docker build process. If you need to rebuild it manually:

```bash
cd olas-sdk-starter/frontend
yarn install
yarn build
```

## Olas SDK Compliance

This agent follows the Olas SDK requirements:

- ✅ Health check endpoint at `/healthcheck` on port `8716`
- ✅ Proper logging format: `[YYYY-MM-DD HH:MM:SS,mmm] [LOG_LEVEL] [agent] message`
- ✅ Docker image naming: `<author>/oar-<agent_name>:<package_hash>`
- ✅ Entrypoint: `python run.py`
- ✅ Environment variable support for configuration

## Troubleshooting

### Agent Not Starting

1. Check that the Ethereum private key is correctly configured
2. Verify all required environment variables are set
3. Check Docker logs: `docker logs pett_agent`

### WebSocket Connection Issues

1. Verify `WEBSOCKET_URL` is correct
2. Check network connectivity
3. Ensure `PRIVY_TOKEN` is valid

### Health Check Failing

1. Verify port `8716` is not blocked
2. Check container logs for errors
3. Ensure the agent process is running inside the container

## Support

For issues related to:

- **Olas SDK**: See [Olas SDK Documentation](https://stack.olas.network/olas-sdk/)
- **Pett.ai Platform**: Contact Pett.ai support
- **Agent Issues**: Check the logs and GitHub issues

## License

This project is licensed under the Apache License 2.0 — see the [LICENSE](LICENSE) file for details. The vendored `olas-sdk-starter` directory retains its own Apache 2.0 LICENSE file.
