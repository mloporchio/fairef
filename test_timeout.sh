#!/bin/bash

QUERY_SIZE=(300 3000 30000)
NODES=(4 7 10 13 16)
SEED=11111

for n in "${NODES[@]}"; do
    f=$(( ($n - 1) / 3 ))
    for q in "${QUERY_SIZE[@]}"; do
        echo "================================"
        echo "Starting: $n nodes, query size $q, and $f timeout failures"
        ./run_test.sh --num_nodes=${n} --seed=${SEED} --max_output_size=${q} --num_timeout=${f}
        echo "Completed: $n nodes, query size $q, and $f timeout failures"
        echo "Starting cleanup phase..."
        ./reset.sh
    done
done