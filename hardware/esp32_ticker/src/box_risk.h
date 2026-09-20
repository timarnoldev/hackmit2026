// The risk model, on the device.
//
// The other half of the project. The polisher corrects a strand after the channel has
// damaged it; this one predicts, before anything is written, how likely a strand is to come
// back wrong. In the full pipeline the encoder uses it to choose between candidate strands.
// On the box there is no encoder to steer, so it is shown instead: every strand on the tape
// carries the risk this model gives it.
//
// hardware/esp32_ticker/tools/export_risk.py generates the weights and
// tools/verify_risk.py diffs this file against dnacodec.risk on real strands.

#pragma once

#include <stddef.h>

namespace boxrisk {

// Is the model compiled in?
bool available();

// Peak working memory, so the caller can report it.
size_t workingBytes();

// Allocate the activation buffers. False when there is not enough memory, in which case
// score() returns a negative number and the caller simply shows no risk.
bool begin();

// Risk in [0, 1] for one strand, higher meaning more likely to be decoded wrongly.
// Returns a negative number when the model is unavailable.
float score(const char *strand, int len);

}  // namespace boxrisk
