#!/bin/bash
#
#   This script orchestrates the testing of the framework's end-to-end latency.
#   The script performs the following steps:
#   1. Launches the Docker stack with the specified number of nodes, seed, and max output size.
#   2. Waits for the chain to be ready.
#   3. Starts the model creator listener in the background.
#   4. Executes the request script to generate multiple requests and measure latency.
#

set -euo pipefail

# Set the global Python interpreter path.
export PYTHON_BIN="/home/linuxbrew/.linuxbrew/bin/python3.13"

# Parse command-line arguments.
NUM_NODES=""
SEED=""
MAX_OUTPUT_SIZE=""
NUM_TIMEOUT=0
NUM_ALTER=0

for arg in "$@"; do
    case $arg in
        --num_nodes=*)
            NUM_NODES="${arg#*=}"
            ;;
        --seed=*)
            SEED="${arg#*=}"
            ;;
        --max_output_size=*)
            MAX_OUTPUT_SIZE="${arg#*=}"
            ;;
        --num_timeout=*)
            NUM_TIMEOUT="${arg#*=}"
            ;;
        --num_alter=*)
            NUM_ALTER="${arg#*=}"
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "$NUM_NODES" ]]; then
    echo "Missing required argument: --num_nodes"
    exit 1
fi

if [[ -z "$SEED" ]]; then
    echo "Missing required argument: --seed"
    exit 1
fi

if [[ -z "$MAX_OUTPUT_SIZE" ]]; then
    echo "Missing required argument: --max_output_size"
    exit 1
fi

if [ ${NUM_TIMEOUT} -gt 0 ] && [ ${NUM_ALTER} -gt 0 ]; then
    echo "Cannot specify both --num_timeout and --num_alter at the same time."
    exit 1
fi

ROOT_DIR=$(pwd)
CHAIN_DIR="${ROOT_DIR}/chain"
SCRIPTS_DIR="${CHAIN_DIR}/scripts"
RESULTS_DIR="${ROOT_DIR}/results/benchmark"
DOCKER_LOG_FILE="${ROOT_DIR}/docker_output.log"
LISTENER_LOG_FILE="${ROOT_DIR}/listener_output.log"
REQUESTS_SCRIPT="custom_benchmark.js"
LISTENER_SCRIPT="modelCreatorApprove.js"

cleanup() {
    echo "Cleanup: stopping listener..."
    # Stop model creator listener
    if [[ -n "${MODEL_CREATOR_PID:-}" ]]; then
        kill -INT "$MODEL_CREATOR_PID" 2>/dev/null || true
    fi
    echo "Cleanup: stopping Docker stack..."
    # Stop docker compose stack
    if [[ -n "${STACK_PID:-}" ]]; then
        kill -INT "$STACK_PID" 2>/dev/null || true
    fi
    echo "Cleanup: forcing Docker shutdown..."
    # Force Docker shutdown.
    cd $ROOT_DIR
    docker compose -f docker-compose.generated.toxiproxy.yml down || true
    echo "Done!"
}

trap cleanup EXIT INT TERM

echo "Starting test script with NUM_NODES=${NUM_NODES}, SEED=${SEED}, MAX_OUTPUT_SIZE=${MAX_OUTPUT_SIZE}".
if [ ${NUM_TIMEOUT} -gt 0 ]; then
    echo "Malicious timeout count: ${NUM_TIMEOUT}"
fi
if [ ${NUM_ALTER} -gt 0 ]; then
    echo "Malicious alter count: ${NUM_ALTER}"
fi

# Launch Docker stack in the background and redirect output to log file.
# The script waits until the chain is ready.
echo "Launching Docker stack..."
STACK_PID=""
if [ ${NUM_TIMEOUT} -gt 0 ]; then
    ./scripts/run_generated_stack_toxiproxy.sh ${NUM_NODES} ${SEED} ${MAX_OUTPUT_SIZE} --malicious-timeout-count ${NUM_TIMEOUT} > ${DOCKER_LOG_FILE} 2>&1 &
    STACK_PID=$!
elif [ ${NUM_ALTER} -gt 0 ]; then
    ./scripts/run_generated_stack_toxiproxy.sh ${NUM_NODES} ${SEED} ${MAX_OUTPUT_SIZE} --malicious-alter-count ${NUM_ALTER} > ${DOCKER_LOG_FILE} 2>&1 &
    STACK_PID=$!
else
    ./scripts/run_generated_stack_toxiproxy.sh ${NUM_NODES} ${SEED} ${MAX_OUTPUT_SIZE} > ${DOCKER_LOG_FILE} 2>&1 &
    STACK_PID=$!
fi
echo "Stack PID: $STACK_PID"
until [ "$(grep -c -- "--- CHAIN READY ---" "${DOCKER_LOG_FILE}")" -ge 2 ]; do
    sleep 1
done
echo "Chain is ready!"

cd $CHAIN_DIR
echo "Launching model creator listener..."
npx hardhat run ${SCRIPTS_DIR}/${LISTENER_SCRIPT} --network localhost > ${LISTENER_LOG_FILE} 2>&1 &
MODEL_CREATOR_PID=$!
echo "Listener PID: $MODEL_CREATOR_PID"
sleep 5 # Wait for the listener to initialize and process any pending events
echo "Listener is ready!"

echo "Running request workload..."
npx hardhat run ${SCRIPTS_DIR}/${REQUESTS_SCRIPT} --network localhost
echo "Request workload completed."

# Process results and save to results directory.
CHAIN_DIR="${ROOT_DIR}/chain"
FAULT_STR="0"
if [ ${NUM_TIMEOUT} -gt 0 ]; then
    FAULT_STR="${NUM_TIMEOUT}t"
fi
if [ ${NUM_ALTER} -gt 0 ]; then
    FAULT_STR="${NUM_ALTER}a"
fi
mkdir -p "${RESULTS_DIR}" # Ensure results directory exists
RESULT_FILE_NAME="benchmark_results_n=${NUM_NODES}_f=${FAULT_STR}_q=${MAX_OUTPUT_SIZE}.csv"
cp "${CHAIN_DIR}/benchmark_results.csv" "${RESULTS_DIR}/${RESULT_FILE_NAME}"