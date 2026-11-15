# Private serving comparison draft

Original 42-cell matrix remains **partial**.

| Cell | Disposition | Actual context | E2E p50/p95 |
| --- | --- | --- | --- |
| baseline-natural-ctx10240-c1 | accepted | 7682.0 | 4451.420110999607/5756.927713984624 |
| baseline-natural-ctx10240-c2 | accepted | 7682.0 | 5468.112965929322/10333.858583006077 |
| baseline-natural-ctx10240-c4 | accepted | 7684.0 | 9144.949725014158/16169.5690570632 |
| baseline-natural-ctx40960-c1 | accepted | 31600.0 | 8588.56604306493/11140.110424021259 |
| baseline-natural-ctx40960-c2 | accepted | 31600.0 | 9658.048425102606/32286.259656073526 |
| baseline-natural-ctx40960-c4 | accepted | 31600.0 | 27230.108070070855/48318.930840003304 |
| baseline-natural-ctx163840-c1 | accepted | 134357.5 | 28572.346618049778/30796.689732931554 |
| baseline-natural-ctx163840-c2 | accepted | 134357.5 | 54354.17980491184/80051.35081906337 |
| baseline-natural-ctx163840-c4 | accepted | 134357.5 | 80036.19651601184/208628.837728058 |
| baseline-controlled-ctx10240-c1 | failed |  | / |
| baseline-controlled-ctx10240-c2 | blocked |  | / |
| baseline-controlled-ctx10240-c4 | blocked |  | / |
| baseline-controlled-ctx40960-c1 | blocked |  | / |
| baseline-controlled-ctx40960-c2 | blocked |  | / |
| baseline-controlled-ctx40960-c4 | blocked |  | / |
| baseline-controlled-ctx163840-c1 | blocked |  | / |
| baseline-controlled-ctx163840-c2 | blocked |  | / |
| baseline-controlled-ctx163840-c4 | blocked |  | / |
| baseline-capacity-edge-ctx310272-c1 | accepted | 252300.5 | 52748.39311896358/54392.5559920026 |
| baseline-capacity-edge-ctx310272-c2 | accepted | 252300.5 | 101335.02844895702/110268.1252489565 |
| baseline-capacity-edge-ctx310272-c4 | accepted | 252300.5 | 198283.12347189058/200938.38414095808 |
| candidate-natural-ctx10240-c1 | accepted | 7682.0 | 2303.5802439553663/3571.2024529930204 |
| candidate-natural-ctx10240-c2 | accepted | 7682.0 | 4115.3902360238135/5556.1179630458355 |
| candidate-natural-ctx10240-c4 | accepted | 7684.0 | 5289.525522035547/8705.540847033262 |
| candidate-natural-ctx40960-c1 | accepted | 31600.0 | 4426.441236981191/4858.702225028537 |
| candidate-natural-ctx40960-c2 | accepted | 31600.0 | 10256.058919010684/11865.369937964715 |
| candidate-natural-ctx40960-c4 | accepted | 31600.0 | 18888.943376019597/22466.961150057614 |
| candidate-natural-ctx163840-c1 | accepted | 134357.5 | 17799.127941951156/18653.25217298232 |
| candidate-natural-ctx163840-c2 | accepted | 134357.5 | 35148.13826803584/38048.34059101995 |
| candidate-natural-ctx163840-c4 | accepted | 134357.5 | 69526.39530203305/73568.51203506812 |
| candidate-controlled-ctx10240-c1 | blocked |  | / |
| candidate-controlled-ctx10240-c2 | blocked |  | / |
| candidate-controlled-ctx10240-c4 | blocked |  | / |
| candidate-controlled-ctx40960-c1 | blocked |  | / |
| candidate-controlled-ctx40960-c2 | blocked |  | / |
| candidate-controlled-ctx40960-c4 | blocked |  | / |
| candidate-controlled-ctx163840-c1 | blocked |  | / |
| candidate-controlled-ctx163840-c2 | blocked |  | / |
| candidate-controlled-ctx163840-c4 | blocked |  | / |
| candidate-capacity-edge-ctx310272-c1 | accepted | 252300.5 | 34235.515908920206/34902.00679807458 |
| candidate-capacity-edge-ctx310272-c2 | accepted | 252300.5 | 67943.82063299417/70028.0758269364 |
| candidate-capacity-edge-ctx310272-c4 | accepted | 252300.5 | 69934.18766302057/138711.5456419997 |
