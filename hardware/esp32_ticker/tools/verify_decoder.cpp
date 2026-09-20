// Host harness for the on-device decoder.
//
// Compiles src/box_decoder.cpp for the Mac so its output can be diffed against
// dnacodec.baseline on the same clusters, without flashing anything. This is the check that
// says the box and the Mac agree about what was decoded.
//
// Build and run:
//   c++ -std=c++17 -O2 -I../src -o /tmp/verify_decoder verify_decoder.cpp ../src/box_decoder.cpp
//   python3 tools/verify_decoder.py          (drives it, see that file)
//
// Protocol on stdin, one cluster per line:
//   <strand_length>\t<read1>\t<read2>...\n
// One line out per cluster:
//   <consensus>\t<medoid draft>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "box_decoder.h"
#include "box_polish.h"

int main() {
  if (const char *o = getenv("BT_ORDER")) boxdec::gBacktraceOrder = atoi(o);
  fprintf(stderr, "bt_order=%d\n", boxdec::gBacktraceOrder);
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;
    std::vector<std::string> fields;
    size_t start = 0;
    while (true) {
      const size_t tab = line.find('\t', start);
      fields.push_back(line.substr(start, tab == std::string::npos ? tab : tab - start));
      if (tab == std::string::npos) break;
      start = tab + 1;
    }
    if (fields.size() < 2) {
      std::cout << "\t\n";
      continue;
    }
    const int strandLength = std::atoi(fields[0].c_str());

    std::vector<const char *> reads;
    std::vector<int> lens;
    for (size_t i = 1; i < fields.size(); ++i) {
      if (fields[i].empty()) continue;
      reads.push_back(fields[i].c_str());
      lens.push_back((int)fields[i].size());
    }
    if (reads.empty()) {
      std::cout << "\t\n";
      continue;
    }

    boxdec::Cluster c;
    boxdec::subsample(reads.data(), lens.data(), (int)reads.size(), boxdec::kMaxReads, c);

    char cons[boxdec::kMaxLen + 1] = {0};
    char draft[boxdec::kMaxLen + 1] = {0};
    boxdec::reconstruct(c, strandLength, 3, cons, sizeof(cons));
    boxdec::pickDraft(c, strandLength, draft, sizeof(draft));

    // the polished strand too, when the model is compiled in
    char polished[boxdec::kMaxLen + 1] = {0};
    char pdraft[boxdec::kMaxLen + 1] = {0};
    if (boxpolish::begin()) boxpolish::polish(c, strandLength, pdraft, sizeof(pdraft),
                                              polished, sizeof(polished));
    std::cout << cons << "\t" << draft << "\t" << polished << "\n";
  }
  return 0;
}
