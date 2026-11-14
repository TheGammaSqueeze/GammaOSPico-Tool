/*
 * card_oracle: the console's own card code on a computer, for the tests.
 *
 * Built against the GammaOS Pico bootloader sources (lk-mt6785, emu/gba) when they are at hand, it
 * reads a FAT32 or exFAT image the way the console does:
 *
 *   card_oracle put  <image> <folder>     copy a folder's files into the image with the console's
 *                                         own FAT writer, in the order the folder lists them
 *   card_oracle list <image> [max]        list the games with the console's own scanner: a line
 *                                         "TOTAL <listed> <found>", then a line per game:
 *                                         type, folder, archive kind, member, size, name, the
 *                                         name it shows (tabs)
 *   card_oracle art  <image>              for each game, the size of the box art the console's
 *                                         loader reads for it (0 none), then its name (tabs)
 *   card_oracle foldcp                    pico_fold_cp of every code point that it changes,
 *                                         "<code point> <folded>" in hex, a line each
 *   card_oracle names                     for each line of stdin (a name in UTF-8): its
 *                                         pico_fold_ascii, gba_sd_legacy_key and whether that
 *                                         differs (tabs)
 *   card_oracle u16  <hex units...>       the name fat_readdir gives for these UTF-16 units, its
 *                                         UTF-8 bytes in hex
 */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include "fat_ro.h"
#include "fat_wr.h"
#include "sd_fat.h"
#include "gba_boxart_sd.h"
#include "gba_boxart.h"
#include "gba_sd_save.h"
#include "pico_fold.h"

static FILE *img;

/* the block layer the console's FAT code reads and writes through */
unsigned long mmc_wrap_bread(int dev, unsigned long blk, unsigned long cnt, void *dst, unsigned int part)
{
	(void)dev; (void)part;
	if (fseek(img, (long)blk * 512, SEEK_SET)) return 0;
	return fread(dst, 512, cnt, img);
}
unsigned long mmc_wrap_bwrite(int dev, unsigned long blk, unsigned long cnt, const void *src, unsigned int part)
{
	(void)dev; (void)part;
	if (fseek(img, (long)blk * 512, SEEK_SET)) return 0;
	return fwrite(src, 512, cnt, img);
}
/* the hardware the card code would bring up on the console: nothing to do here */
int mmc_legacy_init(int verbose) { (void)verbose; return 0; }
void mtk_wdt_restart(void) {}
unsigned gpt4_get_current_tick(void) { return 0; }
int msdc_ext_sd_power_on(void) { return 0; }
int mt_set_gpio_mode(unsigned int pin, unsigned int v) { (void)pin; (void)v; return 0; }
int mt_set_gpio_dir(unsigned int pin, unsigned int v) { (void)pin; (void)v; return 0; }
int mt_set_gpio_pull_enable(unsigned int pin, unsigned int v) { (void)pin; (void)v; return 0; }
int mt_set_gpio_pull_select(unsigned int pin, unsigned int v) { (void)pin; (void)v; return 0; }
int mt_get_gpio_in(unsigned int pin) { (void)pin; return 0; }
void udelay(unsigned long us) { (void)us; }
const char *g_gba_state_dir = "/states/gba";
/* the settings block the save code links to: not used here */
int ayaneo_settings_serialize(unsigned char *b, int cap) { (void)b; (void)cap; return 0; }
void ayaneo_settings_deserialize(const unsigned char *b, int len) { (void)b; (void)len; }

static fat_vol vol;
static unsigned char *filebuf;
#define FILE_MAX (16u << 20)

static int copy_tree(const char *host, const char *card)
{
	DIR *dir = opendir(host);
	struct dirent *de;
	int bad = 0;
	if (!dir) return 1;
	fat_wr_mkpath(&vol, card[0] ? card : "/");
	while ((de = readdir(dir)) != NULL) {
		char hp[1024], cp[1024];
		struct stat st;
		if (de->d_name[0] == '.') continue;
		snprintf(hp, sizeof hp, "%s/%s", host, de->d_name);
		snprintf(cp, sizeof cp, "%s/%s", card, de->d_name);
		if (stat(hp, &st) != 0) continue;
		if (S_ISDIR(st.st_mode)) {
			bad |= copy_tree(hp, cp);
		} else {
			FILE *f = fopen(hp, "rb");
			size_t n;
			if (!f) { bad = 1; continue; }
			n = fread(filebuf, 1, FILE_MAX, f);
			fclose(f);
			if (fat_wr_put(&vol, card[0] ? card : "/", de->d_name, filebuf, (uint32_t)n) != 0) {
				fprintf(stderr, "could not write %s\n", cp);
				bad = 1;
			}
		}
	}
	closedir(dir);
	return bad;
}

int main(int argc, char **argv)
{
	if (argc >= 2 && !strcmp(argv[1], "foldcp")) {
		uint32_t c;
		for (c = 0; c < 0x30000; c++)
			if (pico_fold_cp(c) != c) printf("%x %x\n", c, pico_fold_cp(c));
		return 0;
	}
	if (argc >= 2 && !strcmp(argv[1], "names")) {
		static char line[4096];
		while (fgets(line, sizeof line, stdin)) {
			char fa[600], lk[128];
			int L = (int)strlen(line), d;
			if (L && line[L - 1] == '\n') line[--L] = 0;
			pico_fold_ascii(line, fa, sizeof fa);
			d = gba_sd_legacy_key(line, lk, sizeof lk);
			printf("%s\t%s\t%d\n", fa, lk, d);
		}
		return 0;
	}
	if (argc >= 2 && !strcmp(argv[1], "u16")) {
		uint16_t u[300];
		char out[256];
		int n = 0, i, o;
		for (i = 2; i < argc && n < 300; i++) u[n++] = (uint16_t)strtoul(argv[i], 0, 16);
		o = fat_u16_name(u, n, out, sizeof out);
		for (i = 0; i < o; i++) printf("%02x", (unsigned char)out[i]);
		printf("\n");
		return 0;
	}
	if (argc < 3) { fprintf(stderr, "usage: card_oracle put|list|art <image> ...\n"); return 2; }
	img = fopen(argv[2], "r+b");
	if (!img || gba_sd_mount(&vol) != 0) { fprintf(stderr, "the image could not be mounted\n"); return 1; }
	if (!strcmp(argv[1], "put") && argc >= 4) {
		int rc;
		filebuf = malloc(FILE_MAX);
		rc = copy_tree(argv[3], "");
		fclose(img);
		return rc;
	}
	if (!strcmp(argv[1], "list")) {
		static gba_rom_entry games[32768];
		int max = argc >= 4 ? atoi(argv[3]) : 32768, n, total, i;
		n = gba_sd_list_roms(&vol, games, max, &total);
		printf("TOTAL %d %d\n", n, total);
		for (i = 0; i < n; i++)
			printf("%d\t%s\t%d\t%d\t%u\t%s\t%s\n", games[i].type, gba_sd_rom_dir(&games[i]), games[i].arc,
			       games[i].member, games[i].size, games[i].name, games[i].uname);
		fclose(img);
		return 0;
	}
	if (!strcmp(argv[1], "art")) {
		static gba_rom_entry games[32768];
		static unsigned char buf[1 << 20];
		int n, total, i;
		n = gba_sd_list_roms(&vol, games, 32768, &total);
		for (i = 0; i < n; i++) {
			/* the loader's own order: beside the game, then the system's own folder */
			char dir[112];
			const char *home = gba_boxart_home_dir(games[i].type);
			unsigned got;
			gba_boxart_dir(&games[i], dir, sizeof dir);
			got = gba_sd_read_named(&vol, dir, games[i].name, "ART", buf, sizeof buf);
			if (got < 12 && home && strcasecmp(dir, home) != 0)
				got = gba_sd_read_named(&vol, home, games[i].name, "ART", buf, sizeof buf);
			printf("%u\t%s\n", got < 12 ? 0 : got, games[i].name);
		}
		fclose(img);
		return 0;
	}
	return 2;
}
