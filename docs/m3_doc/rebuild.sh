#!/bin/bash
# rebuild the Milestone 3 document from the latest scored results and copy it into the project
set -e
M=$(cd "$(dirname "$0")" && pwd)
cd /a/subjects/genAI/final_project/blackbox && python -m eval.report --batch eval_v1 >/dev/null 2>&1
cd "$M" && PYTHONIOENCODING=utf-8 python gen_data.py data.json | head -1
node build.js data.json BlackBox_Milestone_3.docx
D=$(cygpath -w "$M"); powershell -NoProfile -ExecutionPolicy Bypass -File topdf.ps1 "$D\BlackBox_Milestone_3.docx" "$D\m3.pdf"
pdfinfo m3.pdf | grep Pages
cp BlackBox_Milestone_3.docx /a/subjects/genAI/final_project/BlackBox_Milestone_3.docx
cp m3.pdf /a/subjects/genAI/final_project/BlackBox_Milestone_3.pdf
cp build.js gen_data.py rebuild.sh /a/subjects/genAI/final_project/blackbox/docs/m3_doc/
