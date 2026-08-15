CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic
CPPFLAGS ?=
LDFLAGS ?=
LDLIBS ?= -lm

ifeq ($(OS),Windows_NT)
EXEEXT := .exe
endif

TARGET := build/svdenc$(EXEEXT)

.PHONY: all clean
all: $(TARGET)

$(TARGET): svdenc.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

build:
ifeq ($(OS),Windows_NT)
	-mkdir build
else
	mkdir -p build
endif

clean:
	$(RM) $(TARGET)
