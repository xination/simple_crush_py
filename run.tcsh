#!/usr/bin/env tcsh

setenv CRUSH_PY_CALLER_CWD "$cwd"
set script_path="$0"
if (! -e "$script_path") then
    set script_path="$cwd/$0"
endif

set script_dir=`dirname "$script_path"`
cd "$script_dir"

python -m crush_py $argv:q
