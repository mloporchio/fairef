/**
 * This script can be used to perform a benchmark of the DON latency by automating the entire request lifecycle.
 * It simulates a customer submitting a request with an IPFS-stored payload, waits for the model creator to approve it,
 * and then listens for the final fulfillment event.
 */

const hre = require("hardhat");
const fs = require("fs");
const { resolveCustomerSigner } = require("./lib/signers");

const NUM_REQUESTS = 16; // Number of requests to perform.
const OUTPUT_FILE = "benchmark_results.csv"; // Path of the output file.
const PROMPT_TEXT = "To be or not to be";

async function main() {
    console.log("[BENCHMARK] Starting automated DON latency evaluation...\n");

    // Dynamic import for 'kubo-rpc-client' (ESM module compatibility)
    const { create } = await import('kubo-rpc-client');
    const ipfsUrl = process.env.IPFS_API_URL || 'http://127.0.0.1:5001';
    const ipfs = create({ url: ipfsUrl });

    const aggregatorAddress = process.env.AGGREGATOR_ADDRESS;

    const { signer: customerWallet, index, signerCount } = await resolveCustomerSigner(hre, aggregatorAddress);
    console.log(`[CHAIN] Using customer signer #${index}/${signerCount - 1}: ${customerWallet.address}`);

    const aggregatorContract = await hre.ethers.getContractAt("Aggregator", aggregatorAddress, customerWallet);
    const queueAddress = await aggregatorContract.queue();
    const verifierAddress = await aggregatorContract.verifier();
    const queueContract = await hre.ethers.getContractAt("OracleQueue", queueAddress);
    const verifierContract = await hre.ethers.getContractAt("OracleVerifier", verifierAddress);

    // Initialize CSV Telemetry Data
    const csvHeader = "Iteration,Timestamp,OffChain_Storage(ms),OnChain_RequestTx(ms),OnChain_ApprovalTx(ms),OCR_Consensus_And_FulfillmentTx(ms),Total_Latency(ms)\n";
    fs.writeFileSync(OUTPUT_FILE, csvHeader);

    for (let i = 1; i <= NUM_REQUESTS; i++) {
        console.log(`========================================`);
        console.log(` ITERATION ${i} / ${NUM_REQUESTS}`);
        console.log(`========================================`);

        // ---------------------------------------------------------------------
        // PHASE 1: Off-Chain Storage (IPFS Upload)
        // ---------------------------------------------------------------------
        console.log("[1/4] Off-Chain Storage (IPFS Upload)...");
        const tIpfsStart = performance.now();
        const { cid } = await ipfs.add(PROMPT_TEXT);
        const tIpfs = performance.now() - tIpfsStart;
        const cidString = cid.toString();
        console.log(`CID: ${cidString}`);
        console.log(`Elapsed time: ${tIpfs.toFixed(3)} ms\n`);

        // ---------------------------------------------------------------------
        // PHASE 2: Customer Request (On-Chain Transaction)
        // ---------------------------------------------------------------------
        // Pre-register listener for the Approval event to avoid race conditions
        const approvalPromise = new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error("Timeout: LogNewJobForOracles event missed")), 45000);
            queueContract.once("LogNewJobForOracles", (jobId) => {
                clearTimeout(timeout);
                resolve(jobId);
            });
        });

        console.log("[2/4] On-Chain Customer Request (TX)...");
        const tPhase1Start = performance.now();
        const paymentAmount = await aggregatorContract.queryFee(); // Read query fee.
        const tx = await aggregatorContract.requestAttribution(cidString, { value: paymentAmount });
        const receipt = await tx.wait();
        const tPhase1 = performance.now() - tPhase1Start;
        console.log(`Elapsed time: ${tPhase1.toFixed(3)} ms\n`);

        // Extract RequestID from the transaction logs
        let currentJobId = null;
        for (const log of receipt.logs) {
            try {
                const parsed = queueContract.interface.parseLog(log);
                if (parsed.name === "LogNewCustomerRequest") {
                    currentJobId = parsed.args[0];
                    break;
                }
            } catch (e) { /* Skip unrelated logs */ }
        }

        // ---------------------------------------------------------------------
        // PHASE 3: Validation & Approval (Model Creator Tx)
        // ---------------------------------------------------------------------
        console.log("[3/4] On-Chain Approval (Model Creator TX)...");
        const tPhase2Start = performance.now();
        const approvedJobId = await approvalPromise; // Resolves when the separate Creator script approves the job
        const tPhase2 = performance.now() - tPhase2Start;
        console.log(`Elapsed time: ${tPhase2.toFixed(3)} ms\n`);

        // ---------------------------------------------------------------------
        // PHASE 4: OCR Network Execution (AI Inference + P2P Consensus)
        // ---------------------------------------------------------------------
        console.log(`[4/4] Off-Chain Reporting (AI + BFT Consensus)...`);
        const tPhase3Start = performance.now();
        let winnerAddress = "";
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error(`Timeout: JobCompleted event missed for Job #${currentJobId}`));
            }, 600000); // 10-minute threshold for AI computation and P2P rounds

            const fulfillmentListener = (jobId, submitter) => {
                if (approvedJobId !== null && jobId.toString() === approvedJobId.toString()) {
                    clearTimeout(timeout);
                    winnerAddress = submitter;
                    verifierContract.off("JobCompleted", fulfillmentListener);
                    resolve();
                }
            };
            verifierContract.on("JobCompleted", fulfillmentListener);
        });
        const tPhase3 = performance.now() - tPhase3Start;
        console.log(`Elapsed time: ${tPhase3.toFixed(3)} ms`);
        console.log(`Transmitter Node Identity: ${winnerAddress}\n`);

        // ---------------------------------------------------------------------
        // TELEMETRY EXPORT
        // ---------------------------------------------------------------------
        const totalTime = tIpfs + tPhase1 + tPhase2 + tPhase3; // Compute total time.

        // Format all timings to 3 decimal places for CSV export.
        const tIpfsStr = tIpfs.toFixed(3);
        const tPhase1Str = tPhase1.toFixed(3);
        const tPhase2Str = tPhase2.toFixed(3);
        const tPhase3Str = tPhase3.toFixed(3);
        const totalTimeStr = totalTime.toFixed(3);
        const csvRow = `${i},${Date.now()},${tIpfsStr},${tPhase1Str},${tPhase2Str},${tPhase3Str},${totalTimeStr}\n`;
        fs.appendFileSync(OUTPUT_FILE, csvRow);
        
        console.log(`[OK] Iteration ${i} saved. Total Latency: ${totalTimeStr} ms\n`);

        // RPC Cooldown period
        await new Promise(resolve => setTimeout(resolve, 3000));
    }
    
    console.log(`\nBenchmark completed successfully! Data exported to: ${OUTPUT_FILE}`);
}

main().catch((error) => {
    console.error("Benchmark encountered a fatal error:", error.message);
    process.exit(1);
});
