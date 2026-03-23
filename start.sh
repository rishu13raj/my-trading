#!/bin/bash

# My Trading Bot - Startup Script
# Usage: ./start.sh [backend|frontend|both]

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
echo -e "${BLUE}║   MY TRADING BOT - STARTUP SCRIPT      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Function to start backend
start_backend() {
    echo -e "${GREEN}▶ Starting Backend...${NC}"
    echo -e "${YELLOW}Activating conda environment: my-trading${NC}"

    eval "$(conda shell.bash hook)"
    conda activate my-trading

    if [ $? -ne 0 ]; then
        echo -e "${RED}✗ Error: Conda environment 'my-trading' not found.${NC}"
        echo -e "${YELLOW}Run this first: conda env create -f environment.yml${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Starting FastAPI server...${NC}"
    cd "$SCRIPT_DIR/backend"
    python main.py
}

# Function to start frontend
start_frontend() {
    echo -e "${GREEN}▶ Starting Frontend...${NC}"

    # Check if asdf is installed
    if ! command -v asdf &> /dev/null; then
        echo -e "${RED}✗ Error: asdf not found. Please install asdf.${NC}"
        exit 1
    fi

    cd "$SCRIPT_DIR/frontend"

    # Check if Node modules are installed
    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}Installing npm dependencies...${NC}"
        npm install
    fi

    echo -e "${YELLOW}Starting HTTP server on port 3001...${NC}"
    npm start
}

# Function to check if services are running
check_services() {
    echo -e "\n${BLUE}Checking services...${NC}"

    if curl -s http://localhost:8000/status > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Backend (port 8000) - OK${NC}"
    else
        echo -e "${RED}✗ Backend (port 8000) - NOT RUNNING${NC}"
    fi

    if curl -s http://localhost:3001 > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Frontend (port 3001) - OK${NC}"
    else
        echo -e "${RED}✗ Frontend (port 3001) - NOT RUNNING${NC}"
    fi
}

# Main script logic
case "${1:-both}" in
    backend)
        start_backend
        ;;
    frontend)
        start_frontend
        ;;
    both)
        echo -e "${YELLOW}Starting both services...${NC}"
        echo -e "${YELLOW}Note: Open another terminal window and run: ./start.sh frontend${NC}\n"
        start_backend
        ;;
    check)
        check_services
        ;;
    *)
        echo -e "${YELLOW}Usage: ./start.sh [backend|frontend|both|check]${NC}"
        echo -e "\n${BLUE}Examples:${NC}"
        echo -e "  ./start.sh both      - Start both (backend in this terminal, run frontend in another)"
        echo -e "  ./start.sh backend   - Start only backend"
        echo -e "  ./start.sh frontend  - Start only frontend"
        echo -e "  ./start.sh check     - Check if services are running"
        exit 0
        ;;
esac
