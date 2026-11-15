# Fixed-five SWE results

All rows use the same five-task denominator. A qualifying result needs complete
official grading, at least four resolved tasks, and both Django and Pylint
sentinels resolved. Every row below has `sentinels=false`; no row authorizes a
promotion. `complete` describes grading completeness, not qualification.

| Lane | Attempted | Resolved / 5 | Graded / 5 | Empty | Complete | Sentinels | Native record |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| GLM official-base baseline | 5 | 2 | 5 | 0 | true | false | [native](native/public-native-draft/baseline-swe-native.json) |
| Coder390 INT4 TP1 initial | 5 | 3 | 3 | 2 | false | false | [native](native/public-native-draft/coder390-tp1-swe-native.json) |
| Coder390 INT4 TP1 medium | 5 | 3 | 5 | 0 | true | false | [native](native/public-native-draft/coder390-tp1-medium-swe-native.json) |
| Coder390 INT8 TP1 medium | 5 | 2 | 5 | 0 | true | false | [native](native/public-native-draft/coder390-int8-tp1-medium-swe-native.json) |
| GLM mixed TP2 no-spec | 5 | 1 | 1 | 4 | false | false | [native](native/public-native-draft/glm-mixed-tp2-nospec-swe-native.json) |
| GLM mixed TP2 thinking-off | 5 | 1 | 2 | 3 | false | false | [native](native/public-native-draft/glm-mixed-tp2-thinking-off-swe-native.json) |
| Qwen QAD TP2 xhigh | 5 | 1 | 2 | 3 | false | false | [native](native/public-native-draft/qwen-qad-tp2-xhigh-swe5-native.json) |
| DeepSeek TP1 low | 5 | 2 | 4 | 1 | false | false | [native](native/public-native-draft-r5/deepseek-tp1-131k-low-swe5-native.json) |
| DeepSeek TP1 max | 5 | 1 | 1 | 4 | false | false | [native](native/public-native-draft-r5/deepseek-tp1-131k-max-swe5-r2-native.json) |
| Qwen EXL3 TP1 xhigh | 5 | 4 | 4 | 1 | false | false | [native](native/public-native-draft-r10/qwen-exl3-schema-r1-tp1-131k-xhigh-swe5-native.json) |
| Qwen EXL3 TP1 medium | 5 | 3 | 5 | 0 | true | false | [native](native/public-native-draft-r10/qwen-exl3-schema-r1-tp1-131k-medium-swe5-native.json) |
| Stock Qwen FP8 TP1 xhigh | 5 | 2 | 4 | 1 | false | false | [native](native/public-native-draft-r16-r2/stock-qwen27-fp8-tp1-bf16kv-131k-xhigh-swe5-native.json) |
| Ignis R4 terminal | 5 | 2 | 2 | 3 | false | false | [native](native/public-native-draft-r18/ignis-health-r4-swe5-r2-native.json) |

The Ignis result resolved Django; Pylint was ungraded with an empty patch, so the required sentinel pair remains unproven and `sentinels=false`.
The table preserves each result rather than ranking unlike configurations.
