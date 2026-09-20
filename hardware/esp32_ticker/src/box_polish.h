// The learned polisher, on the device.
//
// The same network dnacodec/model/polish.py runs: a stem convolution, eight residual blocks
// of two dilated convolutions each at 128 channels, then GroupNorm, GELU and two 1x1 heads.
// Weights are int8 with one scale per output channel, activations are int16 with calibrated
// per tensor scales, and accumulation is int32. GroupNorm statistics are computed per strand
// at run time in float, because they depend on the input and cannot be folded away.
//
// hardware/esp32_ticker/tools/quantize_polish.py generates the weights and is the numpy
// reference for this arithmetic. tools/verify_polish.py diffs this file against the Python
// model on real clusters.
//
// This runs the single draft path, the original polish_clusters(), not the multi draft
// variant the Mac uses in host mode. That is stated in the report and on the screen.

#pragma once

#include <stddef.h>
#include <stdint.h>

#include "box_decoder.h"

namespace boxpolish {

// Is the model compiled in and usable?
bool available();

// Peak working memory, so the caller can report it.
size_t workingBytes();

// Called between blocks so a long inference does not starve the rest of the system. The
// firmware passes a task delay; the host verifier leaves it unset.
void setYield(void (*fn)());

// Allocate the activation buffers. Returns false when there is not enough memory, in which
// case polish() always returns 0 and the caller stays on the classic decoder.
bool begin();

// Polish one cluster: build the draft and the vote columns exactly as the classic decoder
// does, run the network over them, and apply the edits it is confident about.
//
// `draft` receives the classic majority vote answer, `out` the polished strand. Both are
// strandLength long. Returns the number of bases in `out`, or 0 when the cluster is empty
// or the model is unavailable.
int polish(const boxdec::Cluster &c, int strandLength, char *draft, size_t draftCap,
           char *out, size_t outCap);

}  // namespace boxpolish
