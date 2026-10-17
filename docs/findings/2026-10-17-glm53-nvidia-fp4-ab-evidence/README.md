# GLM-5.3 NVIDIA FP4 controlled A/B

The initial candidate context result was **17/18**, versus incumbent **18/18**. After the user authorized investigation, the unchanged candidate scored **18/18** on a full replay and passed all three follow-ups of the originally failed case. The failure did not recur; its cause remains unproven. No configuration repair, performance win, qualification or promotion is claimed.

The exact original r11 container is restored. Direct and authenticated routed smoke/JSON checks passed2/2 each after the final restoration; original configuration hashes are unchanged and the candidate service is absent.

- [Decision and limits](summary.json), [diagnostic result](diagnostic-result.json), [restoration](restoration.json).
- [Configuration](configuration.json), [workloads](workload-manifest.json), [initial run plan](run-plan.json), [diagnostic plan](diagnostic-plan.json).
- Native evidence: [baseline](native/baseline/baseline-native-index.json), [initial candidate](native/candidate/candidate-native-index.json), [first restoration](native/restoration/restoration-native-index.json), [diagnostic and final restoration](native/diagnostic/diagnostic-native-index.json).
- [Verification](verification.json), [artifact inventory](artifact-manifest.json).
- [Sources](source-registry.json), [cause investigation](diagnostic-research.md), [fixture reconstruction](context-fixture-check.json), [failures](friction-log.md), [coverage](coverage-and-gaps.md).

Initial failed artifacts remain retained. Follow-up observations are separate; matching reconstructed fixtures does not prove the original transmitted bytes, and the small sample with different request/cache histories does not establish a general failure rate. Candidate timing, overlap/endurance and repository-scale coding remain untested. The conditional MTP-off arm was not run because the failure did not recur.
