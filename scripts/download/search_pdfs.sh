#!/bin/bash
# Search eastmoney for various document types

# Try different stock codes and document types
stocks="600519 000858 000001 002415 601318 600036 300750 000333 601012 600900"
doc_types=(
    "招股说明书"
    "可转换公司债券募集说明书"
    "重大资产重组"
    "基金招募说明书"
    "法律意见书"
    "资产评估报告"
    "信用评级报告"
    "股权激励计划"
    "配股说明书"
    "年度报告"
    "审计报告"
)

for stock in $stocks; do
    echo "=== Stock: $stock ==="
    # Get announcements for this stock
    curl -sL "http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=SHA&stock_list=$stock&f_node=0&s_node=0" \
      -H "User-Agent: Mozilla/5.0" 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    for item in d.get('data',{}).get('list',[]):
        cols=[c['column_name'] for c in item.get('columns',[])]
        print(f\"{item['art_code']}|{item.get('title_ch','')[:60]}|{cols[0] if cols else ''}|{item.get('notice_date','')}\")
except Exception as e:
    pass
"
done
