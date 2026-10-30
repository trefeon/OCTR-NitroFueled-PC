# Linux launcher CPU-feature check and optional rewrite-table generation.
ISA_REWRITE_ARGS=()
ISA_REWRITE_WANTED=0

isa_rewrite_preflight_check() {
    local probe=$1 features status=0
    ISA_REWRITE_ARGS=()
    ISA_REWRITE_WANTED=0
    if features=$("$probe" --cpu-features); then
        :
    else
        status=$?
        printf '%s\n' "$features"
        return "$status"
    fi
    case ${BB_ISA_REWRITE:-} in
        0) return 0 ;;
        1) ISA_REWRITE_WANTED=1 ;;
        *) if [[ $features == CPU:\ missing\ * ]]; then ISA_REWRITE_WANTED=1; fi ;;
    esac
}

isa_rewrite_preflight_generate() {
    local python=$1 boot=$2 out=$3 output
    ISA_REWRITE_ARGS=()
    [[ $ISA_REWRITE_WANTED == 1 ]] || return 0
    if output=$("$python" scripts/isa_rewrite.py "$boot" --out "$out"); then
        if [[ -n $output ]]; then printf '%s\n' "$output"; fi
        ISA_REWRITE_ARGS=(--isa-rewrite "$out/isa-rewrite.bin")
    else
        if [[ -n $output ]]; then printf '%s\n' "$output"; fi
    fi
    return 0
}
