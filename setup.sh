#!/bin/bash

# My Trading Bot - Setup Script
# Installs all dependencies (one-time setup)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   MY TRADING BOT - SETUP SCRIPT        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Check prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"

if ! command -v conda &> /dev/null; then
    echo -e "${RED}✗ conda not found. Please install conda first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ conda found${NC}"

if ! command -v asdf &> /dev/null; then
    echo -e "${RED}✗ asdf not found. Please install asdf first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ asdf found${NC}"

# Check .env file
echo -e "\n${BLUE}Checking .env file...${NC}"
if [ ! -f ".env" ]; then
    echo -e "${RED}✗ .env file not found.${NC}"
    exit 1
fi

# Check if API_SECRET is set
if grep -q "^API_SECRET=your_api_secret_here" .env; then
    echo -e "${RED}✗ API_SECRET not configured in .env${NC}"
    echo -e "${YELLOW}Please update .env with your Zerodha API credentials first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ .env file configured${NC}"

# Setup Python environment
echo -e "\n${BLUE}Setting up Python environment with conda...${NC}"
if conda env list | grep -q "my-trading"; then
    echo -e "${YELLOW}Conda environment 'my-trading' already exists.${NC}"
else
    echo -e "${YELLOW}Creating conda environment 'my-trading'...${NC}"
    conda env create -f environment.yml
fi

# Verify Python setup
echo -e "${YELLOW}Verifying Python packages...${NC}"
eval "$(conda shell.bash hook)"
conda activate my-trading

python -c "import kiteconnect; import fastapi; import pandas" 2>/dev/null && \
    echo -e "${GREEN}✓ Python packages verified${NC}" || \
    echo -e "${YELLOW}⚠ Some packages may need updating${NC}"

# Setup Node environment
echo -e "\n${BLUE}Setting up Node.js with asdf...${NC}"
asdf install nodejs 2>/dev/null || echo -e "${YELLOW}Node.js installation completed (or already installed)${NC}"
echo -e "${GREEN}✓ Node.js version set via asdf${NC}"

# Setup frontend
echo -e "\n${BLUE}Setting up frontend dependencies...${NC}"
cd "$SCRIPT_DIR/frontend"
npm install
echo -e "${GREEN}✓ Frontend dependencies installed${NC}"

# Success message
echo -e "\n${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        SETUP COMPLETED SUCCESSFULLY    ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

echo -e "${BLUE}Next steps:${NC}"
echo -e "1. Open .env and verify all credentials are correct"
echo -e "2. Open two terminal windows"
echo -e "3. In terminal 1: ${YELLOW}./start.sh backend${NC}"
echo -e "4. In terminal 2: ${YELLOW}./start.sh frontend${NC}"
echo -e "5. Open browser: ${YELLOW}http://localhost:3000${NC}"
echo -e "6. Click 'Authenticate Zerodha' to get started\n"

echo -e "${BLUE}Or simply run: ${YELLOW}./start.sh both${NC}${BLUE} and open frontend in another terminal${NC}\n"
