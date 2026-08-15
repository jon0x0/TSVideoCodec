/* Portable SVD ECM encoder core.  Build as ISO C11 with GCC or Clang. */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIDTH 256
#define HEIGHT 192
#define CELLS (HEIGHT * 32)
#define RGB_BYTES (WIDTH * HEIGHT * 3)

typedef struct {
    float brightness, contrast, saturation, gamma;
    float attr_penalty, pixel_penalty, stable_multiplier;
    float flat_ordered_variance, flat_solid_variance;
    float flat_solid_background_distance;
    int flat_solid_max_y;
    int flat_ordered_attribute;
    float flat_ordered_mix;
    int serpentine;
    const char *input, *output_pix, *output_atr;
    const char *previous_pix, *previous_atr, *stable_cells;
} Options;

typedef struct { int index; int64_t improvement; } RankedCell;

static const uint8_t palette_rgb[16][3] = {
    {0,0,0}, {0,0,205}, {205,0,0}, {205,0,205},
    {0,205,0}, {0,205,205}, {205,205,0}, {205,205,205},
    {96,96,96}, {2,0,253}, {255,2,1}, {255,2,253},
    {0,255,28}, {2,255,255}, {255,255,29}, {255,255,255}
};

static size_t screen_offset(int y, int xb) {
    return (size_t)(((y & 0xc0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | xb);
}

static void die(const char *message) {
    fprintf(stderr, "svdenc: %s\n", message);
    exit(1);
}

static void read_exact(const char *path, void *data, size_t size) {
    FILE *handle = fopen(path, "rb");
    if (!handle) { fprintf(stderr, "svdenc: cannot open %s: %s\n", path, strerror(errno)); exit(1); }
    if (fread(data, 1, size, handle) != size || fgetc(handle) != EOF) {
        fprintf(stderr, "svdenc: %s must contain exactly %zu bytes\n", path, size); exit(1);
    }
    fclose(handle);
}

static void write_exact(const char *path, const void *data, size_t size) {
    FILE *handle = fopen(path, "wb");
    if (!handle) { fprintf(stderr, "svdenc: cannot write %s: %s\n", path, strerror(errno)); exit(1); }
    if (fwrite(data, 1, size, handle) != size || fclose(handle) != 0) {
        fprintf(stderr, "svdenc: failed writing %s\n", path); exit(1);
    }
}

static float parse_float(const char *name, const char *text) {
    char *end = NULL;
    float value = strtof(text, &end);
    if (!end || *end) { fprintf(stderr, "svdenc: invalid %s: %s\n", name, text); exit(2); }
    return value;
}

static float srgb_to_linear(uint8_t channel) {
    float value = (float)channel / 255.0f;
    return value <= 0.04045f ? value / 12.92f : powf((value + 0.055f) / 1.055f, 2.4f);
}

static Options parse_options(int argc, char **argv) {
    Options o = {0.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, INFINITY, 192, -1, -1.0f, 1,
                 NULL, NULL, NULL, NULL, NULL, NULL};
    if (argc < 5 || strcmp(argv[1], "sierra") != 0) {
        die("usage: svdenc sierra INPUT.rgb OUTPUT.pix OUTPUT.atr [options]");
    }
    o.input = argv[2]; o.output_pix = argv[3]; o.output_atr = argv[4];
    for (int i = 5; i < argc; ++i) {
        const char *name = argv[i];
        if (!strcmp(name, "--no-serpentine")) { o.serpentine = 0; continue; }
        if (i + 1 >= argc) die("missing option value");
        const char *value = argv[++i];
        if (!strcmp(name, "--brightness")) o.brightness = parse_float(name, value);
        else if (!strcmp(name, "--contrast")) o.contrast = parse_float(name, value);
        else if (!strcmp(name, "--saturation")) o.saturation = parse_float(name, value);
        else if (!strcmp(name, "--gamma")) o.gamma = parse_float(name, value);
        else if (!strcmp(name, "--temporal-attr-penalty")) o.attr_penalty = parse_float(name, value);
        else if (!strcmp(name, "--temporal-pixel-penalty")) o.pixel_penalty = parse_float(name, value);
        else if (!strcmp(name, "--stable-penalty-multiplier")) o.stable_multiplier = parse_float(name, value);
        else if (!strcmp(name, "--flat-ordered-variance")) o.flat_ordered_variance = parse_float(name, value);
        else if (!strcmp(name, "--flat-solid-variance")) o.flat_solid_variance = parse_float(name, value);
        else if (!strcmp(name, "--flat-solid-background-distance")) o.flat_solid_background_distance = parse_float(name, value);
        else if (!strcmp(name, "--flat-solid-max-y")) {
            char *end = NULL; long parsed = strtol(value, &end, 10);
            if (!end || *end || parsed < 0 || parsed > HEIGHT) die("invalid --flat-solid-max-y");
            o.flat_solid_max_y = (int)parsed;
        }
        else if (!strcmp(name, "--flat-ordered-attribute")) {
            char *end = NULL; long parsed = strtol(value, &end, 0);
            if (!end || *end || parsed < -1 || parsed > 127) die("invalid --flat-ordered-attribute");
            o.flat_ordered_attribute = (int)parsed;
        }
        else if (!strcmp(name, "--flat-ordered-mix")) o.flat_ordered_mix = parse_float(name, value);
        else if (!strcmp(name, "--previous-pix")) o.previous_pix = value;
        else if (!strcmp(name, "--previous-atr")) o.previous_atr = value;
        else if (!strcmp(name, "--stable-cells")) o.stable_cells = value;
        else { fprintf(stderr, "svdenc: unknown option %s\n", name); exit(2); }
    }
    if ((o.previous_pix == NULL) != (o.previous_atr == NULL))
        die("--previous-pix and --previous-atr must be supplied together");
    if (o.contrast <= 0 || o.saturation < 0 || o.gamma <= 0 ||
        o.attr_penalty < 0 || o.pixel_penalty < 0 || o.stable_multiplier < 1 ||
        o.flat_ordered_variance < 0 || o.flat_solid_variance < 0 || o.flat_solid_background_distance < 0 ||
        o.flat_ordered_mix < -1 || o.flat_ordered_mix > 1)
        die("invalid adjustment or temporal parameter");
    return o;
}

static size_t hybrid_plane_size(const uint8_t *previous, const uint8_t *current) {
    uint32_t *cost = malloc((CELLS + 1) * sizeof(*cost));
    uint8_t *delta = malloc(CELLS);
    if (!cost || !delta) die("out of memory");
    for (int i = 0; i < CELLS; ++i) delta[i] = previous[i] ^ current[i];
    cost[CELLS] = 0;
    for (int position = CELLS - 1; position >= 0; --position) {
        uint32_t best = UINT32_MAX;
        if (delta[position] == 0) {
            for (int run = 1; run <= 127 && position + run <= CELLS && delta[position + run - 1] == 0; ++run) {
                uint32_t value = 1 + cost[position + run]; if (value < best) best = value;
            }
        }
        int maximum = CELLS - position < 64 ? CELLS - position : 64;
        for (int run = 1; run <= maximum; ++run) {
            uint32_t value = 1u + (uint32_t)run + cost[position + run]; if (value < best) best = value;
        }
        if (position + 8 <= CELLS) {
            int changed = 0; for (int i = 0; i < 8; ++i) changed += delta[position + i] != 0;
            if (changed) { uint32_t value = 2u + (uint32_t)changed + cost[position + 8]; if (value < best) best = value; }
        }
        cost[position] = best;
    }
    size_t result = (size_t)cost[0] + 1; free(cost); free(delta); return result;
}

static void pixel_colour(const uint8_t *pix, const uint8_t *atr, int y, int x, const uint8_t **colour) {
    int xb = x >> 3; size_t offset = screen_offset(y, xb); uint8_t attribute = atr[offset];
    int bright = attribute & 0x40 ? 8 : 0;
    int paper = bright | ((attribute >> 3) & 7), ink = bright | (attribute & 7);
    *colour = palette_rgb[(pix[offset] & (0x80 >> (x & 7))) ? ink : paper];
}

static int compare_ranked(const void *left, const void *right) {
    const RankedCell *a = left, *b = right;
    if (a->improvement != b->improvement) return a->improvement < b->improvement ? 1 : -1;
    return a->index < b->index ? 1 : (a->index > b->index ? -1 : 0);
}

static int rate_hybrid_main(int argc, char **argv) {
    if (argc != 10 && argc != 12 && argc != 14) die("usage: svdenc rate-hybrid INPUT.rgb PREV.pix PREV.atr CAND.pix CAND.atr OUTPUT.pix OUTPUT.atr BUDGET [--forced-cells FILE] [--forced-cell-bonus N]");
    uint8_t *rgb = malloc(RGB_BYTES), *prev_pix = malloc(CELLS), *prev_atr = malloc(CELLS);
    uint8_t *cand_pix = malloc(CELLS), *cand_atr = malloc(CELLS), *out_pix = malloc(CELLS), *out_atr = malloc(CELLS);
    RankedCell *ranked = malloc(CELLS * sizeof(*ranked));
    uint8_t *forced = NULL;
    int64_t forced_bonus = 250000;
    if (!rgb || !prev_pix || !prev_atr || !cand_pix || !cand_atr || !out_pix || !out_atr || !ranked) die("out of memory");
    read_exact(argv[2], rgb, RGB_BYTES); read_exact(argv[3], prev_pix, CELLS); read_exact(argv[4], prev_atr, CELLS);
    read_exact(argv[5], cand_pix, CELLS); read_exact(argv[6], cand_atr, CELLS);
    char *end = NULL; long budget = strtol(argv[9], &end, 10); if (!end || *end || budget <= 0) die("invalid rate budget");
    for (int i = 10; i < argc; i += 2) {
        if (!strcmp(argv[i], "--forced-cells")) {
            forced = malloc(CELLS); if (!forced) die("out of memory"); read_exact(argv[i + 1], forced, CELLS);
        } else if (!strcmp(argv[i], "--forced-cell-bonus")) {
            char *bonus_end = NULL; forced_bonus = strtoll(argv[i + 1], &bonus_end, 10);
            if (!bonus_end || *bonus_end || forced_bonus < 0) die("invalid forced-cell bonus");
        } else die("unknown rate-hybrid option");
    }
    int ranked_count = 0;
    for (int y = 0; y < HEIGHT; ++y) for (int xb = 0; xb < 32; ++xb) {
        int64_t improvement = 0;
        for (int bit = 0; bit < 8; ++bit) {
            int x = xb * 8 + bit, pixel = (y * WIDTH + x) * 3; const uint8_t *old_colour, *new_colour;
            pixel_colour(prev_pix, prev_atr, y, x, &old_colour); pixel_colour(cand_pix, cand_atr, y, x, &new_colour);
            for (int k = 0; k < 3; ++k) {
                int old_delta = (int)rgb[pixel + k] - old_colour[k], new_delta = (int)rgb[pixel + k] - new_colour[k];
                improvement += old_delta * old_delta - new_delta * new_delta;
            }
        }
        int logical = y * 32 + xb;
        /* Prefer deferred restoration without letting stale cells monopolize
           the complete frame budget.  This bonus is below a strong 8-pixel
           source improvement but above small residual colour errors. */
        if (forced && forced[logical]) improvement += forced_bonus;
        if (improvement > 0) ranked[ranked_count++] = (RankedCell){logical, improvement};
    }
    qsort(ranked, (size_t)ranked_count, sizeof(*ranked), compare_ranked);
    int low = 0, high = ranked_count, best_count = 0; size_t best_size = 0;
    while (low <= high) {
        int middle = (low + high) / 2; memcpy(out_pix, prev_pix, CELLS); memcpy(out_atr, prev_atr, CELLS);
        for (int i = 0; i < middle; ++i) { int logical = ranked[i].index, y = logical / 32, xb = logical % 32; size_t off = screen_offset(y, xb); out_pix[off] = cand_pix[off]; out_atr[off] = cand_atr[off]; }
        size_t size = hybrid_plane_size(prev_pix, out_pix) + hybrid_plane_size(prev_atr, out_atr);
        if (size <= (size_t)budget) { best_count = middle; best_size = size; low = middle + 1; } else high = middle - 1;
    }
    memcpy(out_pix, prev_pix, CELLS); memcpy(out_atr, prev_atr, CELLS);
    for (int i = 0; i < best_count; ++i) { int logical = ranked[i].index, y = logical / 32, xb = logical % 32; size_t off = screen_offset(y, xb); out_pix[off] = cand_pix[off]; out_atr[off] = cand_atr[off]; }
    write_exact(argv[7], out_pix, CELLS); write_exact(argv[8], out_atr, CELLS);
    printf("%zu %d\n", best_size, best_count);
    free(rgb); free(prev_pix); free(prev_atr); free(cand_pix); free(cand_atr); free(out_pix); free(out_atr); free(ranked); free(forced);
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "rate-hybrid")) return rate_hybrid_main(argc, argv);
    Options o = parse_options(argc, argv);
    uint8_t *rgb = malloc(RGB_BYTES), *bitmap = calloc(CELLS, 1), *attrs = malloc(CELLS);
    uint8_t *previous_bitmap = NULL, *previous_attrs = NULL, *stable = NULL;
    float *source = malloc((size_t)RGB_BYTES * sizeof(float));
    float *work = malloc((size_t)RGB_BYTES * sizeof(float));
    float *cell_papers = malloc((size_t)CELLS * 3 * sizeof(float));
    float *cell_inks = malloc((size_t)CELLS * 3 * sizeof(float));
    float *flat_mix = calloc(CELLS, sizeof(float)); uint8_t *flat_cells = calloc(CELLS, 1);
    uint8_t background_attrs[HEIGHT];
    if (!rgb || !bitmap || !attrs || !source || !work || !cell_papers || !cell_inks || !flat_mix || !flat_cells) die("out of memory");
    read_exact(o.input, rgb, RGB_BYTES);
    if (o.previous_pix) {
        previous_bitmap = malloc(CELLS); previous_attrs = malloc(CELLS);
        if (!previous_bitmap || !previous_attrs) die("out of memory");
        read_exact(o.previous_pix, previous_bitmap, CELLS);
        read_exact(o.previous_atr, previous_attrs, CELLS);
    }
    if (o.stable_cells) { stable = malloc(CELLS); if (!stable) die("out of memory"); read_exact(o.stable_cells, stable, CELLS); }

    float palette[16][3];
    for (int c = 0; c < 16; ++c) for (int k = 0; k < 3; ++k)
        palette[c][k] = srgb_to_linear(palette_rgb[c][k]);
    for (int p = 0; p < WIDTH * HEIGHT; ++p) {
        float adjusted[3], luma;
        for (int k = 0; k < 3; ++k)
            adjusted[k] = (srgb_to_linear(rgb[p * 3 + k]) - 0.18f) * o.contrast + 0.18f + o.brightness;
        luma = adjusted[0] * 0.2126f + adjusted[1] * 0.7152f + adjusted[2] * 0.0722f;
        for (int k = 0; k < 3; ++k) {
            float value = luma + (adjusted[k] - luma) * o.saturation;
            if (value < 0) value = 0;
            value = powf(value, 1.0f / o.gamma);
            if (value > 1) value = 1;
            source[p * 3 + k] = work[p * 3 + k] = value;
        }
    }

    for (int y = 0; y < HEIGHT; ++y) for (int xb = 0; xb < 32; ++xb) {
        int logical = y * 32 + xb, best_attr = 0; float best_error = INFINITY;
        for (int bright_index = 0; bright_index < 2; ++bright_index) {
            int base = bright_index ? 8 : 0;
            for (int paper = 0; paper < 8; ++paper) for (int ink = 0; ink < 8; ++ink) {
                int attr = (bright_index ? 0x40 : 0) | (paper << 3) | ink;
                float axis[3], denominator = 0, error = 0;
                for (int k = 0; k < 3; ++k) { axis[k] = palette[base | ink][k] - palette[base | paper][k]; denominator += axis[k] * axis[k]; }
                for (int px = 0; px < 8; ++px) {
                    const float *pixel = &source[(y * WIDTH + xb * 8 + px) * 3]; float numerator = 0;
                    for (int k = 0; k < 3; ++k) numerator += (pixel[k] - palette[base | paper][k]) * axis[k];
                    float projection = denominator > 1e-12f ? numerator / denominator : 0;
                    if (projection < 0) projection = 0; else if (projection > 1) projection = 1;
                    for (int k = 0; k < 3; ++k) { float d = pixel[k] - (palette[base | paper][k] + projection * axis[k]); error += d * d; }
                }
                if (previous_attrs) {
                    float multiplier = stable && stable[logical] ? o.stable_multiplier : 1.0f;
                    if (attr != (previous_attrs[screen_offset(y, xb)] & 0x7f)) error += o.attr_penalty * multiplier;
                }
                if (error < best_error) { best_error = error; best_attr = attr; }
            }
        }
        attrs[screen_offset(y, xb)] = (uint8_t)best_attr;
        int bright = (best_attr & 0x40) ? 8 : 0;
        int paper = bright | ((best_attr >> 3) & 7), ink = bright | (best_attr & 7);
        if (xb == 0) background_attrs[y] = (uint8_t)best_attr;
        for (int k = 0; k < 3; ++k) { cell_papers[logical * 3 + k] = palette[paper][k]; cell_inks[logical * 3 + k] = palette[ink][k]; }
        if (o.flat_ordered_variance > 0 || o.flat_solid_variance > 0) {
            float mean[3] = {0,0,0}, variance = 0, axis[3], numerator = 0, denominator = 0;
            float background_mean[3] = {0,0,0}, background_distance = 0;
            for (int px = 0; px < 8; ++px) for (int k = 0; k < 3; ++k)
                mean[k] += source[(y * WIDTH + xb * 8 + px) * 3 + k] / 8.0f;
            for (int px = 0; px < 8; ++px) for (int k = 0; k < 3; ++k)
                background_mean[k] += source[(y * WIDTH + px) * 3 + k] / 8.0f;
            for (int k = 0; k < 3; ++k) { float d = mean[k] - background_mean[k]; background_distance += d * d / 3.0f; }
            for (int px = 0; px < 8; ++px) for (int k = 0; k < 3; ++k) {
                float d = source[(y * WIDTH + xb * 8 + px) * 3 + k] - mean[k]; variance += d * d / 24.0f;
            }
            for (int k = 0; k < 3; ++k) { axis[k] = palette[ink][k] - palette[paper][k]; numerator += (mean[k] - palette[paper][k]) * axis[k]; denominator += axis[k] * axis[k]; }
            if (o.flat_solid_variance > 0 && y < o.flat_solid_max_y &&
                variance <= o.flat_solid_variance &&
                background_distance <= o.flat_solid_background_distance) {
                int nearest = 0; float nearest_error = INFINITY;
                for (int colour = 0; colour < 16; ++colour) {
                    float error = 0; for (int k = 0; k < 3; ++k) { float d = mean[k] - palette[colour][k]; error += d * d; }
                    if (error < nearest_error) { nearest_error = error; nearest = colour; }
                }
                int index = nearest & 7;
                int solid_attr = (nearest >= 8 ? 0x40 : 0) | (index << 3) | index;
                attrs[screen_offset(y, xb)] = (uint8_t)solid_attr;
                for (int k = 0; k < 3; ++k) cell_papers[logical * 3 + k] = cell_inks[logical * 3 + k] = palette[nearest][k];
                flat_mix[logical] = 0; flat_cells[logical] = 1;
            } else if (o.flat_ordered_variance > 0 && y < o.flat_solid_max_y &&
                       variance <= o.flat_ordered_variance &&
                       background_distance <= o.flat_solid_background_distance) {
                int background_attr = o.flat_ordered_attribute >= 0
                    ? o.flat_ordered_attribute : background_attrs[y];
                int background_bright = (background_attr & 0x40) ? 8 : 0;
                paper = background_bright | ((background_attr >> 3) & 7);
                ink = background_bright | (background_attr & 7);
                attrs[screen_offset(y, xb)] = (uint8_t)background_attr;
                numerator = denominator = 0;
                for (int k = 0; k < 3; ++k) {
                    cell_papers[logical * 3 + k] = palette[paper][k];
                    cell_inks[logical * 3 + k] = palette[ink][k];
                    axis[k] = palette[ink][k] - palette[paper][k];
                    numerator += (background_mean[k] - palette[paper][k]) * axis[k];
                    denominator += axis[k] * axis[k];
                }
                float mix = o.flat_ordered_mix >= 0 ? o.flat_ordered_mix
                    : denominator > 1e-12f ? numerator / denominator : 0;
                flat_mix[logical] = mix < 0 ? 0 : mix > 1 ? 1 : mix; flat_cells[logical] = 1;
            }
        }
    }

    static const uint8_t bayer8[8][8] = {
        {0,48,12,60,3,51,15,63}, {32,16,44,28,35,19,47,31},
        {8,56,4,52,11,59,7,55}, {40,24,36,20,43,27,39,23},
        {2,50,14,62,1,49,13,61}, {34,18,46,30,33,17,45,29},
        {10,58,6,54,9,57,5,53}, {42,26,38,22,41,25,37,21}
    };
    for (int y = 0; y < HEIGHT; ++y) {
        int rtl = o.serpentine && (y & 1), start = rtl ? WIDTH - 1 : 0, end = rtl ? -1 : WIDTH, direction = rtl ? -1 : 1;
        for (int x = start; x != end; x += direction) {
            int xb = x >> 3, logical = y * 32 + xb, p = y * WIDTH + x;
            float ink_error = 0, paper_error = 0, value[3];
            for (int k = 0; k < 3; ++k) {
                value[k] = work[p * 3 + k]; if (value[k] < 0) value[k] = 0; else if (value[k] > 1) value[k] = 1;
                float di = value[k] - cell_inks[logical * 3 + k], dp = value[k] - cell_papers[logical * 3 + k];
                ink_error += di * di; paper_error += dp * dp;
            }
            if (previous_bitmap) {
                float multiplier = stable && stable[logical] ? o.stable_multiplier : 1.0f;
                int old_ink = previous_bitmap[screen_offset(y, xb)] & (0x80 >> (x & 7));
                if (old_ink) paper_error += o.pixel_penalty * multiplier; else ink_error += o.pixel_penalty * multiplier;
            }
            int use_ink = flat_cells[logical]
                ? flat_mix[logical] > ((float)bayer8[y & 7][x & 7] + 0.5f) / 64.0f
                : ink_error < paper_error;
            if (use_ink) bitmap[screen_offset(y, xb)] |= (uint8_t)(0x80 >> (x & 7));
            for (int k = 0; k < 3; ++k) {
                float chosen = use_ink ? cell_inks[logical * 3 + k] : cell_papers[logical * 3 + k];
                float error = flat_cells[logical] ? 0.0f : value[k] - chosen; int nx = x + direction, back = x - direction;
                if (nx >= 0 && nx < WIDTH) work[(y * WIDTH + nx) * 3 + k] += error * 0.5f;
                if (y + 1 < HEIGHT) {
                    if (back >= 0 && back < WIDTH) work[((y + 1) * WIDTH + back) * 3 + k] += error * 0.25f;
                    work[((y + 1) * WIDTH + x) * 3 + k] += error * 0.25f;
                }
            }
        }
    }
    write_exact(o.output_pix, bitmap, CELLS); write_exact(o.output_atr, attrs, CELLS);
    free(rgb); free(bitmap); free(attrs); free(previous_bitmap); free(previous_attrs); free(stable);
    free(source); free(work); free(cell_papers); free(cell_inks); free(flat_mix); free(flat_cells);
    return 0;
}
