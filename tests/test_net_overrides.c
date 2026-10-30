#define _GNU_SOURCE
#include "runtime_net_overrides.h"

#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#include <direct.h>
#define make_dir(path) _mkdir(path)
#define remove_dir(path) _rmdir(path)
#define set_data_dir() _putenv("BB_DATA_DIR=out/net-overrides-test")
#else
#include <sys/stat.h>
#include <unistd.h>
#define make_dir(path) mkdir(path, 0700)
#define remove_dir(path) rmdir(path)
#define set_data_dir() setenv("BB_DATA_DIR", "out/net-overrides-test", 1)
#endif

static int failures;
#define CHECK(cond) do { if (!(cond)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

int main(void) {
    make_dir("out/net-overrides-test");
    FILE *f = fopen("out/net-overrides-test/host_overrides.json", "w");
    if (!f) {
        perror("host_overrides.json");
        return 1;
    }
    fputs("{\n"
          "  \"pipes-prod-glutton.public.aws.demonware.net\": \"127.0.0.1\",\n"
          "  \"objectstore.prod.demonware.net\": \"127.0.0.1\",\n"
          "  \"lavender-ps4-auth3.prod.demonware.net\": \"127.0.0.1\",\n"
          "  \"explicit.example.test\": \"127.0.0.1\"\n"
          "}\n", f);
    fclose(f);
    set_data_dir();
    net_overrides_init(); /* Load stale entries before proving the hard deny wins. */

    CHECK(net_overrides_lookup("pipes-prod-glutton.public.aws.demonware.net", NULL, NULL) == NULL);
    CHECK(net_overrides_lookup("objectstore.prod.demonware.net", NULL, NULL) == NULL);
    CHECK(!net_overrides_is_allowed("pipes-prod-glutton.public.aws.demonware.net"));
    CHECK(!net_overrides_is_allowed("PiPeS-PrOd-GlUtToN.PuBlIc.AwS.DeMoNwArE.NeT"));
    CHECK(!net_overrides_is_allowed("objectstore.prod.demonware.net"));
    CHECK(!net_overrides_is_allowed("ObJeCtStOrE.PrOd.DeMoNwArE.NeT"));
    CHECK(net_overrides_is_allowed("lavender-ps4-auth3.prod.demonware.net"));
    CHECK(net_overrides_is_allowed("explicit.example.test"));
    CHECK(!net_overrides_is_allowed("unlisted.example.test"));
    CHECK(net_overrides_is_allowed("localhost"));
    CHECK(net_overrides_is_allowed("127.0.0.1"));

    remove("out/net-overrides-test/host_overrides.json");
    remove_dir("out/net-overrides-test");
    if (failures) {
        fprintf(stderr, "FAIL: %d checks\n", failures);
        return 1;
    }
    puts("PASS: telemetry hosts stay blocked despite explicit overrides; unlisted hosts are denied");
    return 0;
}
