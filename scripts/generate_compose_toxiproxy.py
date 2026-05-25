#!/usr/bin/env python3
"""Generate a Docker Compose stack that routes oracle P2P traffic through Toxiproxy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import generate_compose as base


TOXIPROXY_COMPOSE_FILE = "docker-compose.generated.toxiproxy.yml"
TOXIPROXY_IMAGE = "ghcr.io/shopify/toxiproxy:2.12.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--toxiproxy-image", default=TOXIPROXY_IMAGE)
    parser.add_argument("--toxiproxy-latency-ms", type=int, default=None)
    parser.add_argument("--toxiproxy-jitter-ms", type=int, default=0)
    parser.add_argument("--oracle-p2p-port", default=None)
    parser.add_argument("--toxiproxy-base-ip", default="10.5.1.20")
    parser.add_argument("--toxiproxyctl-ip", default="10.5.1.10")
    tox_args, remaining = parser.parse_known_args()

    old_argv = sys.argv
    try:
        sys.argv = [old_argv[0], *remaining]
        args = base.parse_args()
    finally:
        sys.argv = old_argv

    if not any(arg == "--out" or arg.startswith("--out=") for arg in remaining):
        args.out = TOXIPROXY_COMPOSE_FILE
    args.enable_latency = False
    args.toxiproxy_image = tox_args.toxiproxy_image
    args.toxiproxy_latency_ms = tox_args.toxiproxy_latency_ms
    args.toxiproxy_jitter_ms = tox_args.toxiproxy_jitter_ms
    args.oracle_p2p_port = tox_args.oracle_p2p_port
    args.toxiproxy_base_ip = tox_args.toxiproxy_base_ip
    args.toxiproxyctl_ip = tox_args.toxiproxyctl_ip
    return args


def read_env_file_value(path: Path, key: str, default: str) -> str:
    if not path.exists():
        return default

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'") or default
    return default


def toxiproxy_latencies(
    args: argparse.Namespace,
    repo_root: Path,
    oracle_locations: list[str],
) -> list[int]:
    if args.toxiproxy_latency_ms is not None:
        return [args.toxiproxy_latency_ms for _ in range(args.num_oracles)]

    matrix = base.load_latency_matrix(repo_root / base.AZURE_LATENCY_CSV)
    latencies: list[int] = []
    for destination_index, destination in enumerate(oracle_locations):
        values: list[float] = []
        for source_index, source in enumerate(oracle_locations):
            if source_index == destination_index:
                continue
            value = matrix.get(source, {}).get(destination, "")
            if value:
                values.append(float(value))
        latencies.append(round(sum(values) / len(values)) if values else 0)
    return latencies


def write_toxiproxy_files(
    args: argparse.Namespace,
    repo_root: Path,
    env_file_path: Path,
    oracle_locations: list[str],
) -> None:
    output_dir = repo_root / "generated" / "toxiproxy"
    output_dir.mkdir(parents=True, exist_ok=True)

    p2p_port = args.oracle_p2p_port or read_env_file_value(
        env_file_path,
        "ORACLE_P2P_PORT",
        "20010",
    )

    for idx in range(args.num_oracles):
        config = [
            {
                "name": f"oracle{idx}_p2p",
                "listen": f"0.0.0.0:{p2p_port}",
                "upstream": f"oracle{idx}:{p2p_port}",
                "enabled": True,
            }
        ]
        (output_dir / f"oracle{idx}.json").write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )

    latencies = toxiproxy_latencies(args, repo_root, oracle_locations)
    configure = [
        "#!/bin/sh",
        "set -eu",
        "",
        "wait_for_api() {",
        '    host="$1"',
        "    tries=60",
        '    while [ "$tries" -gt 0 ]; do',
        '        if curl -fsS "http://${host}:8474/version" >/dev/null; then',
        "            return 0",
        "        fi",
        '        tries="$((tries - 1))"',
        "        sleep 1",
        "    done",
        '    echo "[TOXIPROXY] Timed out waiting for ${host}:8474" >&2',
        "    return 1",
        "}",
        "",
        "add_latency() {",
        '    host="$1"',
        '    proxy="$2"',
        '    stream="$3"',
        '    latency="$4"',
        '    jitter="$5"',
        '    name="latency_${stream}"',
        "    curl -fsS -X POST -H 'Content-Type: application/json' \\",
        "        -d \"{\\\"name\\\":\\\"${name}\\\",\\\"type\\\":\\\"latency\\\",\\\"stream\\\":\\\"${stream}\\\",\\\"toxicity\\\":1.0,\\\"attributes\\\":{\\\"latency\\\":${latency},\\\"jitter\\\":${jitter}}}\" \\",
        '        "http://${host}:8474/proxies/${proxy}/toxics" >/dev/null',
        "}",
        "",
    ]
    for idx, latency_ms in enumerate(latencies):
        configure.extend(
            [
                f'host="toxiproxy{idx}"',
                f'proxy="oracle{idx}_p2p"',
                f'latency_ms="{latency_ms}"',
                f'jitter_ms="{args.toxiproxy_jitter_ms}"',
                'echo "[TOXIPROXY] Configuring ${proxy} on ${host}: ${latency_ms}ms +/- ${jitter_ms}ms"',
                'wait_for_api "$host"',
                'curl -fsS -X POST "http://${host}:8474/reset" >/dev/null',
                'add_latency "$host" "$proxy" upstream "$latency_ms" "$jitter_ms"',
                'add_latency "$host" "$proxy" downstream "$latency_ms" "$jitter_ms"',
                "",
            ]
        )

    configure_script = output_dir / "configure.sh"
    configure_script.write_text("\n".join(configure), encoding="utf-8")
    configure_script.chmod(0o755)


def render_compose_toxiproxy(
    args: argparse.Namespace,
    repo_root: Path,
    output_path: Path,
    hardhat_config_path: Path,
    deploy_script_path: Path,
    env_file_path: Path,
    oracle_private_keys: list[str],
    oracle_ips: list[str],
    oracle_locations: list[str],
    config_digest: str,
) -> str:
    compose_dir = output_path.parent
    chain_context = base.compose_path(compose_dir, repo_root / "chain")
    oracle_context = base.compose_path(compose_dir, repo_root / "oracle")
    hardhat_config = base.compose_path(compose_dir, hardhat_config_path)
    deploy_script = base.compose_path(compose_dir, deploy_script_path)
    env_file = base.compose_path(compose_dir, env_file_path)
    ipfs_data = base.compose_path(compose_dir, repo_root / "IpfsAgent" / "ipfs_data")
    ipfs_staging = base.compose_path(compose_dir, repo_root / "IpfsAgent" / "ipfs_staging")
    toxiproxy_config_dir = base.compose_path(compose_dir, repo_root / "generated" / "toxiproxy")
    toxiproxy_ips = base.generate_oracle_ips(args.toxiproxy_base_ip, args.num_oracles)
    malicious_oracle_modes = base.assign_malicious_oracle_modes(args)

    common_env = {
        "CHAIN_RPC": "${CHAIN_RPC_URL}",
        "AGGREGATOR_ADDRESS": "${AGGREGATOR_ADDRESS}",
        "IPFS_API_URL": "${DOCKER_IPFS_URL}",
        "MALICIOUS_MODE": "false",
        "NUM_ORACLES": str(args.num_oracles),
        "FAULT_TOLERANCE": str(args.fault_tolerance),
        "MAX_OUTPUT_SIZE": str(args.max_output_size),
        "NUM_HOLDERS": str(args.num_holders),
        "OCR_SEED": str(args.ocr_seed),
        "NETWORK_SEED": str(args.network_seed),
        "ORACLE_IPS": ",".join(oracle_ips),
        "ORACLE_LOCATIONS": ",".join(oracle_locations),
        "LATENCY_MATRIX_FILE": base.LATENCY_MATRIX_CONTAINER_PATH,
        "ENABLE_LATENCY": "false",
        "LATENCY_BACKEND": "toxiproxy",
    }

    lines = [
        "# Generated by scripts/generate_compose_toxiproxy.py. Do not edit by hand.",
        f"# NUM_ORACLES={args.num_oracles}",
        f"# NETWORK_SEED={args.network_seed}",
        f"# KEY_SEED={args.key_seed}",
        f"# MALICIOUS_ALTER_COUNT={args.malicious_alter_count}",
        f"# MALICIOUS_TIMEOUT_COUNT={args.malicious_timeout_count}",
        f"# MAX_OUTPUT_SIZE={args.max_output_size}",
        f"# NUM_HOLDERS={args.num_holders}",
        "# LATENCY_BACKEND=toxiproxy",
        "",
        "x-generated-oracle-keys: &generated-oracle-keys",
    ]
    if args.malicious_count:
        lines.extend(
            [
                f"# MALICIOUS_COUNT={args.malicious_count}",
                f"# MALICIOUS_MODE={args.malicious_mode}",
            ]
        )
    for idx, private_key in enumerate(oracle_private_keys):
        lines.append(f"  ORACLE{idx}_PRIVATE_KEY: {base.yaml_quote(private_key)}")

    lines.extend(["", "x-oracle-common-env: &oracle-common-env", "  <<: *generated-oracle-keys"])
    lines.extend(base.environment_block(common_env, indent=2))

    lines.extend(
        [
            "",
            "services:",
            "  model_service:",
            "    container_name: model_service",
            f"    build: {base.yaml_quote(base.compose_path(compose_dir, repo_root / 'model'))}",
            "    ports:",
            '      - "9090:9090"',
            "    environment:",
            f"      MAX_OUTPUT_SIZE: {base.yaml_quote(args.max_output_size)}",
            "    networks:",
            "      ocr-net:",
            f"        ipv4_address: 10.5.1.250",
            "    restart: unless-stopped",
            "",
            "  chain:",
            "    container_name: chain",
            f"    build: {base.yaml_quote(chain_context)}",
            "    ports:",
            '      - "8545:8545"',
            "    networks:",
            "      ocr-net:",
            f"        ipv4_address: {args.chain_ip}",
            "    env_file:",
            f"      - {base.yaml_quote(env_file)}",
            "    environment:",
            f"      NUM_ORACLES: {base.yaml_quote(args.num_oracles)}",
            f"      NUM_HOLDERS: {base.yaml_quote(args.num_holders)}",
            f"      FAULT_TOLERANCE: {base.yaml_quote(args.fault_tolerance)}",
            f"      CONFIG_DIGEST: {base.yaml_quote(config_digest)}",
            "    volumes:",
            f"      - {base.yaml_quote(f'{hardhat_config}:/app/hardhat.config.js:ro')}",
            f"      - {base.yaml_quote(f'{deploy_script}:/app/scripts/deployContracts.js:ro')}",
            "",
            "  ipfs:",
            "    image: ipfs/kubo:latest",
            "    container_name: ipfs_node",
            "    restart: always",
            "    ports:",
            '      - "4001:4001"',
            '      - "5001:5001"',
            '      - "8080:8080"',
            "    environment:",
            "      IPFS_PROFILE: server",
            "    volumes:",
            f"      - {base.yaml_quote(f'{ipfs_data}:/data/ipfs')}",
            f"      - {base.yaml_quote(f'{ipfs_staging}:/export')}",
            "    networks:",
            "      ocr-net:",
            f"        ipv4_address: {args.ipfs_ip}",
            "",
            "  bootstrap:",
            f"    build: {base.yaml_quote(oracle_context)}",
            "    env_file:",
            f"      - {base.yaml_quote(env_file)}",
            "    environment:",
            "      <<: *oracle-common-env",
        ]
    )
    lines.extend(
        base.command_block(
            [
                "oracle",
                "-mode",
                "bootstrap",
                "-n",
                str(args.num_oracles),
                "-f",
                str(args.fault_tolerance),
                "-seed",
                str(args.ocr_seed),
                "-bootstrap_listen",
                "0.0.0.0:${BOOTSTRAP_PORT}",
                "-bootstrap_announce",
                "${BOOTSTRAP_ADDR}",
            ]
        )
    )
    lines.extend(
        [
            "    ports:",
            '      - "19900:19900"',
            "    networks:",
            "      ocr-net:",
            f"        ipv4_address: {args.bootstrap_ip}",
            "    depends_on:",
            "      chain:",
            "        condition: service_started",
            "",
            "  toxiproxyctl:",
            "    image: curlimages/curl:8.10.1",
            "    networks:",
            "      ocr-net:",
            f"        ipv4_address: {args.toxiproxyctl_ip}",
            "    volumes:",
            f"      - {base.yaml_quote(f'{toxiproxy_config_dir}:/toxiproxy-generated:ro')}",
            "    entrypoint:",
            "      - sh",
            "      - -lc",
            "    command:",
            "      - /toxiproxy-generated/configure.sh",
            "    depends_on:",
        ]
    )
    for idx in range(args.num_oracles):
        lines.append(f"      - toxiproxy{idx}")

    for idx, (private_key, ip_address, toxiproxy_ip) in enumerate(
        zip(oracle_private_keys, oracle_ips, toxiproxy_ips)
    ):
        lines.extend(
            [
                "",
                f"  toxiproxy{idx}:",
                f"    image: {base.yaml_quote(args.toxiproxy_image)}",
                f"    container_name: toxiproxy_oracle{idx}",
                "    command:",
                "      - -host=0.0.0.0",
                "      - -config=/etc/toxiproxy/toxiproxy.json",
                "    volumes:",
                f"      - {base.yaml_quote(f'{toxiproxy_config_dir}/oracle{idx}.json:/etc/toxiproxy/toxiproxy.json:ro')}",
                "    networks:",
                "      ocr-net:",
                f"        ipv4_address: {toxiproxy_ip}",
                "",
                f"  oracle{idx}:",
                f"    container_name: node_oracle{idx}",
                f"    hostname: oracle{idx}",
                f"    build: {base.yaml_quote(oracle_context)}",
                "    env_file:",
                f"      - {base.yaml_quote(env_file)}",
                "    extra_hosts:",
                '      - "host.docker.internal:host-gateway"',
                "    entrypoint:",
                '      - "/usr/local/bin/wait-for-deploy.sh"',
                "    environment:",
                "      <<: *oracle-common-env",
                f"      PRIVATE_KEY: {base.yaml_quote(private_key)}",
                f"      ORACLE_ID: {base.yaml_quote(idx)}",
                f"      MALICIOUS_MODE: {base.yaml_quote(malicious_oracle_modes.get(idx, 'false'))}",
            ]
        )
        lines.extend(
            base.command_block(
                [
                    "oracle",
                    "-mode",
                    "oracle",
                    "-n",
                    str(args.num_oracles),
                    "-f",
                    str(args.fault_tolerance),
                    "-oracle_id",
                    str(idx),
                    "-seed",
                    str(args.ocr_seed),
                    "-bootstrap_addr",
                    "${BOOTSTRAP_ADDR}",
                    "-p2p_listen",
                    "0.0.0.0:${ORACLE_P2P_PORT}",
                    "-p2p_announce",
                    f"toxiproxy{idx}:${{ORACLE_P2P_PORT}}",
                ]
            )
        )
        lines.extend(
            [
                "    networks:",
                "      ocr-net:",
                f"        ipv4_address: {ip_address}",
                "    depends_on:",
                "      chain:",
                "        condition: service_started",
                "      bootstrap:",
                "        condition: service_started",
                f"      toxiproxy{idx}:",
                "        condition: service_started",
                "      toxiproxyctl:",
                "        condition: service_completed_successfully",
            ]
        )

    lines.extend(
        [
            "",
            "networks:",
            "  ocr-net:",
            "    driver: bridge",
            "    ipam:",
            "      config:",
            f"        - subnet: {args.subnet}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    output_path = base.resolve_repo_path(repo_root, args.out)
    hardhat_config_path = base.resolve_repo_path(repo_root, args.hardhat_config)
    deploy_script_path = base.resolve_repo_path(repo_root, args.deploy_script)
    digest_cache_path = base.resolve_repo_path(repo_root, args.digest_cache)
    env_file_path = base.resolve_repo_path(repo_root, args.env_file)

    deployer_key = args.deployer_private_key.removeprefix("0x")
    if len(deployer_key) != 64:
        raise SystemExit("--deployer-private-key must be a 32-byte hex key")

    used_keys = {deployer_key.lower()}
    oracle_private_keys = [
        base.derive_oracle_private_key(str(args.key_seed), idx, used_keys)
        for idx in range(args.num_oracles)
    ]
    customer_private_key = base.derive_customer_private_key(str(args.key_seed), used_keys)
    oracle_ips = base.generate_oracle_ips(args.oracle_base_ip, args.num_oracles)
    oracle_locations = base.assign_oracle_locations(repo_root, args)
    config_digest = base.compute_config_digest(
        repo_root,
        args,
        oracle_private_keys,
        digest_cache_path,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    hardhat_config_path.parent.mkdir(parents=True, exist_ok=True)
    deploy_script_path.parent.mkdir(parents=True, exist_ok=True)
    write_toxiproxy_files(args, repo_root, env_file_path, oracle_locations)

    hardhat_config_path.write_text(
        base.render_hardhat_config(args, deployer_key, oracle_private_keys, customer_private_key),
        encoding="utf-8",
    )
    deploy_script_path.write_text(
        base.render_deploy_script(args, config_digest),
        encoding="utf-8",
    )
    output_path.write_text(
        render_compose_toxiproxy(
            args=args,
            repo_root=repo_root,
            output_path=output_path,
            hardhat_config_path=hardhat_config_path,
            deploy_script_path=deploy_script_path,
            env_file_path=env_file_path,
            oracle_private_keys=oracle_private_keys,
            oracle_ips=oracle_ips,
            oracle_locations=oracle_locations,
            config_digest=config_digest,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {base.display_path(repo_root, output_path)}")
    print(f"Wrote {base.display_path(repo_root, hardhat_config_path)}")
    print(f"Wrote {base.display_path(repo_root, deploy_script_path)}")
    print("Wrote generated/toxiproxy/")
    print(f"Digest cache: {base.display_path(repo_root, digest_cache_path)}")
    print(f"Computed CONFIG_DIGEST={config_digest}")
    print(f"Oracle locations: {', '.join(oracle_locations)}")
    print()
    print("Run:")
    print(
        f"  docker compose -f {base.display_path(repo_root, output_path)} "
        f"--env-file {base.display_path(repo_root, env_file_path)} up --build"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
