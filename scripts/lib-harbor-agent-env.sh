#!/usr/bin/env bash

cb2tb_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/.." && pwd
}

cb2tb_harbor_bin() {
  if [[ -n "${HARBOR_BIN:-}" ]]; then
    printf '%s\n' "${HARBOR_BIN}"
    return 0
  fi

  command -v harbor || true
}

cb2tb_require_harbor_bin() {
  local harbor_bin
  harbor_bin="$(cb2tb_harbor_bin)"
  if [[ -z "${harbor_bin}" || ! -x "${harbor_bin}" ]]; then
    printf 'missing harbor binary: set HARBOR_BIN or add harbor to PATH\n' >&2
    return 1
  fi
}

cb2tb_source_env_file() {
  local env_file=$1
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "${env_file}"
    set +a
  fi
}

cb2tb_normalize_service_base_url() {
  local url=${1:-} dns_suffix=${CB2TB_SERVICE_DNS_SUFFIX:-}
  if [[ -z "${url}" ]]; then
    printf '\n'
    return 0
  fi

  case "${url}" in
    *".svc:"*)
      if [[ -n "${dns_suffix}" ]]; then
        printf '%s\n' "${url/.svc:/.svc.${dns_suffix}:}"
      else
        printf '%s\n' "${url}"
      fi
      ;;
    *)
      printf '%s\n' "${url}"
      ;;
  esac
}

cb2tb_url_host() {
  local url=${1:?missing URL} authority host
  case "${url}" in
    *://*) authority=${url#*://} ;;
    *)
      printf 'URL must include a scheme: %s\n' "${url}" >&2
      return 1
      ;;
  esac

  authority=${authority%%/*}
  authority=${authority%%\?*}
  authority=${authority##*@}
  case "${authority}" in
    \[*\]*)
      host=${authority#\[}
      host=${host%%\]*}
      ;;
    *)
      host=${authority%%:*}
      ;;
  esac

  host=${host%.}
  if [[ -z "${host}" ]]; then
    printf 'unable to extract host from URL: %s\n' "${url}" >&2
    return 1
  fi
  printf '%s\n' "${host,,}"
}

cb2tb_model_api_proxy_url() {
  local url=${1:?missing URL} remainder suffix
  case "${url}" in
    *://*) remainder=${url#*://} ;;
    *)
      printf 'URL must include a scheme: %s\n' "${url}" >&2
      return 1
      ;;
  esac

  case "${remainder}" in
    */*) suffix=/${remainder#*/} ;;
    *) suffix= ;;
  esac
  printf 'http://model-api-proxy:8080%s\n' "${suffix}"
}

cb2tb_route_claude_through_model_proxy() {
  CB2TB_API_UPSTREAM=${ANTHROPIC_BASE_URL:?missing ANTHROPIC_BASE_URL}
  ANTHROPIC_BASE_URL="$(cb2tb_model_api_proxy_url "${CB2TB_API_UPSTREAM}")"
  export CB2TB_API_UPSTREAM ANTHROPIC_BASE_URL
}

cb2tb_route_openai_through_model_proxy() {
  CB2TB_API_UPSTREAM=${OPENAI_BASE_URL:?missing OPENAI_BASE_URL}
  OPENAI_BASE_URL="$(cb2tb_model_api_proxy_url "${CB2TB_API_UPSTREAM}")"
  OPENAI_API_BASE=${OPENAI_BASE_URL}
  export CB2TB_API_UPSTREAM OPENAI_BASE_URL OPENAI_API_BASE
}

cb2tb_load_claude_env() {
  local repo_root env_file
  repo_root="$(cb2tb_repo_root)"
  env_file="${CB2TB_CLAUDE_ENV_FILE:-${repo_root}/configs/env/claude-code.env}"

  cb2tb_source_env_file "${env_file}"

  if [[ -n "${CC_BASE_URL:-}" ]]; then
    ANTHROPIC_BASE_URL="${CC_BASE_URL}"
  fi
  if [[ -n "${CC_API_KEY:-}" ]]; then
    ANTHROPIC_API_KEY="${CC_API_KEY}"
  fi

  ANTHROPIC_BASE_URL="$(cb2tb_normalize_service_base_url "${ANTHROPIC_BASE_URL:-}")"

  if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
    ANTHROPIC_API_KEY="${ANTHROPIC_AUTH_TOKEN}"
  fi

  : "${ANTHROPIC_BASE_URL:?missing ANTHROPIC_BASE_URL (set ${env_file} or export CC_BASE_URL/ANTHROPIC_BASE_URL)}"
  : "${ANTHROPIC_API_KEY:?missing ANTHROPIC_API_KEY (set ${env_file} or export CC_API_KEY/ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN)}"

  export ANTHROPIC_BASE_URL
  export ANTHROPIC_API_KEY
  unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
  unset ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL
  unset ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
  unset ANTHROPIC_DEFAULT_HAIKU_MODEL
}

cb2tb_load_openai_env() {
  local repo_root env_file
  repo_root="$(cb2tb_repo_root)"
  env_file="${CB2TB_OPENAI_ENV_FILE:-${repo_root}/configs/env/openai-compatible.env}"

  cb2tb_source_env_file "${env_file}"

  OPENAI_BASE_URL="$(cb2tb_normalize_service_base_url "${OPENAI_BASE_URL:-}")"

  : "${OPENAI_BASE_URL:?missing OPENAI_BASE_URL (set ${env_file} or export OPENAI_BASE_URL)}"
  : "${OPENAI_API_KEY:?missing OPENAI_API_KEY (set ${env_file} or export OPENAI_API_KEY)}"

  export OPENAI_BASE_URL
  export OPENAI_API_KEY
  export OPENAI_API_BASE="${OPENAI_BASE_URL}"
}
