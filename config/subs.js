// seeds subscribers 999700000000001..N (default 60); runs automatically when mongo first starts
const n = typeof N === "undefined" ? 60 : N;
for (let i = 1; i <= n; i++) {
  const imsi = "99970" + String(i).padStart(10, "0");
  db.subscribers.updateOne({imsi}, {$set: {
    imsi,
    security: {k: "465B5CE8B199B49FAA5F0A2EE238A6BC", opc: "E8ED289DEB0F7F5E9E2F2D6D3C8C5BF4", amf: "8000", sqn: NumberLong("0")},
    ambr: {downlink: {value: 1, unit: 3}, uplink: {value: 1, unit: 3}},
    slice: [{sst: 1, default_indicator: true, session: [{name: "internet", type: 3,
      qos: {index: 9, arp: {priority_level: 8, pre_emption_capability: 1, pre_emption_vulnerability: 1}},
      ambr: {downlink: {value: 1, unit: 3}, uplink: {value: 1, unit: 3}}}]}],
    access_restriction_data: 32, network_access_mode: 0, subscriber_status: 0,
    operator_determined_barring: 0, subscribed_rau_tau_timer: 12, schema_version: 1
  }}, {upsert: true});
}
print("subscribers: " + db.subscribers.countDocuments());
