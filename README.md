# Fair Remuneration for AI Training Data: a Trustworthy Blockchain-based Approach

This repository contains the implementation of a framework for remunerating training data contributors for generative AI models.  

The framework leverages blockchain technology, specifically the Ethereum blockchain, and a Decentralized Oracle Network (DON) using Chainlink's Off-Chain Reporting (OCR) protocol.

The contribution of each data provider to a given model output is quantified based on the influence scores returned by Training Data Attribution (TDA) methods. The remuneration is then automatically distributed according to customizable logic implemented through smart contracts.

## Architecture

You can find a detailed description of the architecture of the system in the [architecture documentation](./docs/Architecture.md).

## Contributors

This codebase was developed by the following contributors:

- **Federico Tomellini**, University of Pisa, Italy
- **Calogero Turco**, University of Pisa, Italy
- **Matteo Loporchio**, University of Pisa, Italy

## Project structure

The project is organized into the following main directories:

* **chain**: Contains the Solidity smart contracts, Hardhat configuration, and deployment scripts.
* **docs**: Contains additional documentation files for the project.
* **model**: Contains the Python code for the AI model and the corresponding attribution method.
* **oracle**: Contains the off-chain oracle node backend written in Go, including the custom OCR3 (Off-Chain Reporting v3) plugin and listener.
* **scripts**: Automation helpers for generating parametrized Docker Compose stacks and starting experiments.

## Tech stack

The project utilizes a variety of technologies across different components:

* **Blockchain/Smart Contracts:** Solidity, Hardhat, Ethers.js
* **Oracle Infrastructure:** Chainlink DON, Go (Golang), Docker
* **Decentralized Storage:** IPFS (kubo)
* **AI Model & Attribution:** Python, PyTorch, dattri

## Installation and setup

To run this project locally, ensure you have the following installed on your machine:

* **Git**
* **Node.js** (v24.13.0 or higher)
* **Go** (v1.23.4 linux/amd64 or higher)
* **Docker** and **Docker Compose** (v29.2.0 and Docker Compose version v5.0.2)
* **Python** (for the AI backend service)

Follow these steps to set up the project locally:

1. **Clone the repository**
```bash
    git clone https://github.com/Pisa-DLT-Lab/fairef.git
    cd fairef
```
2. **Install smart contract dependencies (Hardhat)**
```bash
    cd chain
    npm install
    cd ..
```
<!--
3. **Install Python dependencies for the AI backend**
```bash
    cd model
    python3 -m venv .venv
    source .venv/bin/activate
    pip3 install dattri torch numpy transformers datasets tiktoken wandb tqdm Flask
```
-->

## How to run

To test the full system, follow this chronological sequence.

<!--
> **Note - Generated Files:** The automation writes `docker-compose.generated.yml`, `generated/hardhat.config.generated.js`, `generated/deployContracts.generated.js`, and `generated/config-digests.json`. These are derived artifacts for a specific experiment configuration.

> **Note - Digest Cache:** The first run for a new configuration computes the OCR config digest. The result is saved in `generated/config-digests.json`, so rerunning the same parameters reuses the cached digest.

> **Note - Byzantine Testing:** Malicious oracle behavior can be injected directly at generation time. By default, both `--malicious-alter-count` and `--malicious-timeout-count` are `0`, so all generated nodes are honest unless you opt in.

-->

<!--
### Step 1: Start the AI module

Before starting the oracle containers, ensure the Python AI module (i.e., the generation and attribution service) is running and accessible at `host.docker.internal:9090`.

To start the service:

```bash
cd model
source .venv/bin/activate
python3 model_server_service.py
```

By default, `model_server_service.py` listens on `0.0.0.0:9090`, which is the address expected by the oracle containers through `host.docker.internal:9090`.

### Step 2: Boot the core infrastructure
-->

### Step 1: Boot the core infrastructure

Thanks to the Docker orchestration, booting the entire ecosystem is highly automated. 

In particular, the **generated stack script** creates a parametrized compose file for a selected number of oracles and network seed.

The generated setup will:

- Start the Hardhat local node.
- Wait for it to be ready.
- Fund the seed-derived oracle accounts plus one extra customer account in Hardhat.
- Deploy the smart contracts with the matching OCR config digest.
- Boot the off-chain oracle nodes.
- Apply deterministic simulated latency among oracle containers by default.

To run the script, open a new terminal in the root directory of the project. Note that, if no `.env` file exists, the runner creates it from `.env.example`. Then execute:

```bash
scripts/run_generated_stack_toxiproxy.sh <NUMBER_OF_ORACLES> <NETWORK_SEED> <MAX_OUTPUT_SIZE> [--malicious-alter-count N] [--malicious-timeout-count M] [--disable-latency]
```

The script requires the following mandatory parameters:

- `NUMBER_OF_ORACLES`: The number of oracle nodes to generate in the stack (e.g., `7`).
- `NETWORK_SEED`: A seed for deterministic generation of oracle keys, latency, and malicious node selection (e.g., `123`).
- `MAX_OUTPUT_SIZE`: Specifies the maximum length of outputs produced by the AI model (expressed in n. of characters).

The script also accepts optional flags for malicious node configuration and latency control:

- `--malicious-alter-count N`: Number of oracles to assign the "alter" malicious behavior (default: `0`). These nodes will alter their response to the Aggregator, simulating data tampering or misreporting.

- `--malicious-timeout-count M`: Number of oracles to assign the "timeout" malicious behavior (default: `0`). These nodes will exhibit timeout behavior, simulating unresponsiveness or denial of service.

You can combine both flags to create mixed malicious node configurations. For example, `--malicious-alter-count 2 --malicious-timeout-count 1` will assign 2 oracles to alter and 1 oracle to timeout behavior. 

Finally, simulated network latency is enabled by default. The optional `--disable-latency` flag allows you to disable the latency among oracle nodes while keeping the generated configuration and Compose stack intact. This can be useful for testing scenarios where latency is not a factor or when you want to isolate other variables in the system.

This script uses the Toxiproxy framework (https://github.com/shopify/toxiproxy) to simulate latency. Note that Toxiproxy-based latency is used by default, which is compatible with all Docker hosts. If you are running on a Linux host with `tc netem` support, you can use the `scripts/run_generated_stack.sh` script instead to apply latency directly at the kernel level (do note that this tool supports network configuration with at most 15 oracles).

**Notice:** It is also possible to specify a custom path for the Python interpreter that will be used by the `scripts/run_generated_stack_toxiproxy.sh` script. To do this, just type

```bash
export PYTHON_BIN="..."
```

before launching the script itself. Replace the dots `...` with the actual path.

**Important**: Once you have launched the script, wait until you see the message:

```
--- CHAIN READY ---
```

in the Docker logs before proceeding.


### Step 2: Simulate the Workflow

Once the Docker infrastructure is up and running, you can simulate the main workflow of the system, which involves interactions between the **model creator** and the **customer** through the deployed smart contracts and oracle network.

#### 1. Start the model creator listener

This script simulates the model creator listening for new requests by the customer. Wait for it to be deployed and then start listening to the events.

To launch the listener, open a new terminal (1) and type:

```bash
cd chain
npx hardhat run scripts/modelCreatorApprove.js --network localhost
```

#### 2a. Trigger the Customer Request

This script simulates the customer sending a request to the system, which will trigger the computation and royalty distribution process. 

To trigger the request, open another terminal (2) and type:

```bash
cd chain
npx hardhat run scripts/customerRequest.js --network localhost
```
To check for latest result in terminal (2):

```bash
cd chain
npx hardhat run scripts/verify.js --network localhost
```

#### 2b. Benchmarking

Alternatively, instead of simulating a single request, you can directly run the benchmark script, which simulates multiple customer requests and measures the performance of the system under load. 

To launch the testing script, open a terminal and type:
```bash
cd chain
npx hardhat run scripts/custom_benchmark.js --network localhost
```

At this point, for each request:

- The generation and attribution computation start.
- The oracle consensus begins.
- Royalties are calculated.

You can monitor everything in the main Docker terminal.

Note that the customer scripts use signer with index `NUM_ORACLES + 1`. Account with index `0` is used for the model creator and accounts `1..NUM_ORACLES` are used for representing oracle nodes.

## How to stop the environment

To stop the execution of containers, press `CTRL+C` in the Docker terminal.

To rebuild the containers, type:

```bash
docker compose -f docker-compose.generated.yml --env-file .env down -v
```

To reset everything (deleting Docker's data), type:

```bash
docker system prune -a -f
```