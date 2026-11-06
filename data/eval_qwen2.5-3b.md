### qwen2.5-3b-instruct (Q4, local CPU)

| metric | value |
|---|---|
| cases | 19 |
| cause accuracy | 63% |
| severity accuracy | 58% |
| cause accuracy, hard cases only | 67% |
| grounded | 100% |
| fell back to rules | 37% |
| mean attempts | 1.84 |
| median latency | 43548 ms |

| case | expected | got | severity (exp / got) |
|---|---|---|---|
| feeder_outage_0 | feeder_outage | feeder_outage (ok) | high / high |
| feeder_outage_1 | feeder_outage | feeder_outage (ok) | critical / critical |
| feeder_outage_2 | feeder_outage | feeder_outage (ok) | high / high |
| transformer_fault_0 | transformer_fault | transformer_fault (ok) | medium / medium |
| transformer_fault_1 | transformer_fault | transformer_fault (ok) | high / high |
| transformer_fault_2 | transformer_fault | transformer_fault (ok) | medium / medium |
| transformer_fault_3 | transformer_fault | transformer_fault (ok) | high / high |
| single_meter_0 | single_meter_outage | transformer_fault (MISS) | low / high |
| single_meter_1 | single_meter_outage | single_meter_outage (ok) | low / medium |
| single_meter_2 | single_meter_outage | single_meter_outage (ok) | medium / high |
| tampering_0 | tampering | tampering (ok) | high / high |
| tampering_1 | tampering | transformer_fault (MISS) | high / high |
| tampering_2 | tampering | transformer_fault (MISS) | high / high |
| voltage_sag | voltage_quality | transformer_fault (MISS) | medium / high |
| voltage_swell | voltage_quality | transformer_fault (MISS) | medium / high |
| comms_loss | communication_loss | transformer_fault (MISS) | low / high |
| hard_partial_phase_loss | transformer_fault | transformer_fault (ok) | medium / high |
| hard_sag_then_outage | transformer_fault | transformer_fault (ok) | high / high |
| hard_lost_neutral | transformer_fault | unknown (MISS) | high / low |
