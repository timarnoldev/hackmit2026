// Host harness for the on-device risk model: one strand per line in, one score per line out.
// Drives box_risk.cpp so tools/verify_risk.py can diff it against dnacodec.risk.

#include <cstdio>
#include <iostream>
#include <string>

#include "box_risk.h"

int main() {
  if (!boxrisk::begin()) {
    std::cerr << "risk model could not allocate\n";
    return 1;
  }
  std::string line;
  while (std::getline(std::cin, line)) {
    while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
    if (line.empty()) continue;
    printf("%.9f\n", (double)boxrisk::score(line.c_str(), (int)line.size()));
  }
  return 0;
}
