/* Static web assets, embedded at build time by codegen/gen_assets.py. */
#ifndef THERMIQ_ASSETS_H
#define THERMIQ_ASSETS_H

#include <stddef.h>

struct asset {
    const char *path;
    const char *content_type;
    const unsigned char *data;
    size_t len;
};

extern const struct asset ASSETS[];
extern const int ASSET_COUNT;

#endif /* THERMIQ_ASSETS_H */
