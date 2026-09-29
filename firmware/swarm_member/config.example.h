// config.example.h: TEMPLATE for firmware/swarm_member/config.h
// ------------------------------------------------------------------------------------------
// scripts/gen_keys.py (Track A) generates config.h from this file with the board's identity
// and its cluster's committee. config.h is GITIGNORED because it holds the board's PRIVATE KEY
// and you type the WiFi password into it locally. Never commit config.h.
// Spec: docs/00_CONTEXT.md §10 (crypto), §11 (wire), §15 (keys); brief: docs/02 §5 B6.
// ------------------------------------------------------------------------------------------
#pragma once
#include <stdint.h>

// ---- network (edit in config.h, locally) -------------------------------------------------
#define WIFI_SSID          "swarmshield"      // hotspot: 2.4 GHz, WPA2-Personal (ESP32 can't do 5 GHz/WPA3-only)
#define WIFI_PASSWORD      "CHANGE_ME"        // type locally in config.h; NEVER commit
#define HUB_IP             "0.0.0.0"          // laptop A Wi-Fi IPv4 (ipconfig), where swarm.station runs
#define HUB_PORT           47000
#define LOCAL_UDP_PORT     47010
#define REGISTER_INTERVAL_MS 2000

// ---- identity (filled by gen_keys.py) ----------------------------------------------------
#define NODE_NAME          "c1-board"
#define NODE_ADDRESS       "0x0000000000000000000000000000000000000000"                          // lowercase
#define NODE_PRIVKEY_HEX   "0000000000000000000000000000000000000000000000000000000000000000"  // SECRET
#define MISSION_ID_HEX     "0000000000000000000000000000000000000000000000000000000000000000"
#define CLUSTER_ID         1

// ---- committee of CLUSTER_ID, sorted ascending by address (filled by gen_keys.py) ----------
// weight: full validators 3, hardware light validators 1. flags bit0 = hardware.
// pubkeys (65-byte uncompressed, hex without 0x) are used only by firmware L3 signature checks.
#define COMMITTEE_SIZE 5
static const char* const COMMITTEE_ADDR[COMMITTEE_SIZE] = {
  "0x0000000000000000000000000000000000000001",
  "0x0000000000000000000000000000000000000002",
  "0x0000000000000000000000000000000000000003",
  "0x0000000000000000000000000000000000000004",
  "0x0000000000000000000000000000000000000005"
};
static const uint16_t COMMITTEE_WEIGHT[COMMITTEE_SIZE] = { 3, 3, 3, 3, 1 };
static const uint8_t  COMMITTEE_FLAGS[COMMITTEE_SIZE]  = { 0, 0, 0, 0, 1 };
static const char* const COMMITTEE_PUBKEY[COMMITTEE_SIZE] = { "", "", "", "", "" };
