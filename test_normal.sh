#!/bin/bash

QUERY_SIZE=(300 3000 30000)
NODES=(4 7 10 13 16)
SEED=11111

for n in "${NODES[@]}"; do
    for q in "${QUERY_SIZE[@]}"; do
        echo "================================"
        echo "Starting: $n nodes, query size $q"
        ./run_test.sh --num_nodes=${n} --seed=${SEED} --max_output_size=${q}
        echo "Completed: $n nodes, query size $q"
        echo "Starting cleanup phase..."
        ./reset.sh
    done
done