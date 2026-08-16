; TS2068 ECM raster-stream TAP player. Loaded as one contiguous RAM block.

                ORG     $7800

PORT_HSR        EQU     $F4
PORT_CTRL       EQU     $FF
SYS_FRAMES      EQU     $5C78
CHNG_VID        EQU     $0E8E
VIDMOD          EQU     $5CC2
WORKSPACE_BACKUP EQU    $E000
WORKSPACE_SIZE  EQU     $1800

                INCLUDE "tap_config.inc"

START:          LD      (ORIGINAL_SP),SP
                DI
                LD      HL,$6000
                LD      DE,WORKSPACE_BACKUP
                LD      BC,WORKSPACE_SIZE
                LDIR                        ; preserve all RAM used as ECM attrs
                LD      SP,$FF00
                LD      HL,$6000
                LD      DE,$6001
                LD      BC,$17FF
                LD      (HL),0
                LDIR                        ; hide initial bitmap construction
                LD      A,2                 ; SCLD Extended Color Mode
                OUT     (PORT_CTRL),A
                LD      HL,0
                LD      (SCHED_ACC),HL
                CALL    RESET_SEQUENCE
WAIT_RELEASE:   CALL    KEY_PRESSED
                JR      NZ,WAIT_RELEASE

NEXT_FRAME:     CALL    NEXT_INTERVAL
                LD      B,A
                LD      A,(SYS_FRAMES)
                ADD     A,B
                LD      (NEXT_TICK),A
                LD      HL,(TABLE_PTR)
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      (TABLE_PTR),HL
                EX      DE,HL
                LD      A,(HL)
                INC     HL
                CP      1
                JR      Z,COPY_KEY
                CP      7
                JR      Z,COPY_PACK_KEY
                CP      4
                JP      NZ,EXIT_PLAYER
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                EX      DE,HL
                CALL    DECODE_RASTER
                JR      FRAME_READY

COPY_KEY:       LD      E,(HL)
                INC     HL
                LD      D,(HL)
                EX      DE,HL
                LD      DE,$4000
                LD      BC,$1800
                LDIR
                LD      DE,$6000
                LD      BC,$1800
                LDIR

                JR      FRAME_READY

COPY_PACK_KEY:  LD      E,(HL)
                INC     HL
                LD      D,(HL)
                EX      DE,HL               ; HL=combined packed planes
                LD      DE,$4000
                LD      BC,$1800
                CALL    DECODE_PACKBITS
                LD      DE,$6000
                LD      BC,$1800
                CALL    DECODE_PACKBITS

FRAME_READY:    EI
HOLD:           HALT
                CALL    KEY_PRESSED
                JP      NZ,EXIT_PLAYER
                LD      A,(SYS_FRAMES)
                LD      B,A
                LD      A,(NEXT_TICK)
                SUB     B
                JR      Z,HOLD_DONE
                JP      M,HOLD_DONE
                JR      HOLD
HOLD_DONE:                                  ; keep ROM clock live during RAM decode
                LD      A,(FRAMES_LEFT)
                DEC     A
                LD      (FRAMES_LEFT),A
                JR      NZ,NEXT_FRAME

PAUSE_LAST:     CALL    RESET_LOOP
                JR      NEXT_FRAME

RESET_SEQUENCE: LD      HL,FRAME_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                RET

RESET_LOOP:     LD      HL,LOOP_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                RET

NEXT_INTERVAL:  LD      HL,(SCHED_ACC)
                LD      DE,TICK_NUMERATOR
                ADD     HL,DE
                LD      B,0
INTERVAL_LOOP:  LD      DE,TICK_DENOMINATOR
                OR      A
                SBC     HL,DE
                JR      C,INTERVAL_DONE
                INC     B
                JR      INTERVAL_LOOP
INTERVAL_DONE:  ADD     HL,DE
                LD      (SCHED_ACC),HL
                LD      A,B
                RET

; Select all keyboard half-rows. NZ means at least one key is held.
KEY_PRESSED:    XOR     A
                IN      A,($FE)
                CPL
                AND     $1F
                RET

; Restore normal display, BASIC's stack, and return from RANDOMIZE USR.
EXIT_PLAYER:    DI
                XOR     A
                OUT     (PORT_CTRL),A
                OUT     (PORT_HSR),A
                LD      (VIDMOD),A
EXIT_RETURN:
                LD      HL,WORKSPACE_BACKUP
                LD      DE,$6000
                LD      BC,WORKSPACE_SIZE
                LDIR
EXIT_STACK_RESTORED:
                LD      SP,(ORIGINAL_SP)
                EI
                RET

; HL=packed source, DE=destination, BC=exact output length. HL continues at
; the next plane, allowing bitmap and attributes to share one contiguous blob.
DECODE_PACKBITS:
PACK_NEXT:      LD      A,(HL)
                INC     HL
                BIT     7,A
                JR      NZ,PACK_RUN
                INC     A
                LD      (PACK_COUNT),A
PACK_LITERAL:   LD      A,(HL)
                INC     HL
                LD      (DE),A
                INC     DE
                DEC     BC
                LD      A,(PACK_COUNT)
                DEC     A
                LD      (PACK_COUNT),A
                JR      NZ,PACK_LITERAL
                JR      PACK_CHECK
PACK_RUN:       AND     $7F
                ADD     A,3
                LD      (PACK_COUNT),A
                LD      A,(HL)
                INC     HL
                LD      (PACK_VALUE),A
PACK_RUN_LOOP:  LD      A,(PACK_VALUE)
                LD      (DE),A
                INC     DE
                DEC     BC
                LD      A,(PACK_COUNT)
                DEC     A
                LD      (PACK_COUNT),A
                JR      NZ,PACK_RUN_LOOP
PACK_CHECK:     LD      A,B
                OR      C
                RET     Z
                JR      PACK_NEXT

; Raster replacement decoder: 00=end, 01=skip u16, 02=bitmap,
; 03=attribute, 04=bitmap/attribute pairs. Runs never cross a row.
DECODE_RASTER:  LD      IX,$4000
                LD      IY,$6000
                LD      DE,BITMAP_ROWS+2
                LD      (NEXT_ROW_PTR),DE
RASTER_COMMAND: LD      A,(HL)
                INC     HL
                OR      A
                RET     Z
                CP      1
                JR      Z,RASTER_SKIP
                CP      2
                JR      Z,RASTER_BITMAP
                CP      3
                JR      Z,RASTER_ATTRIBUTE
                CP      4
                JR      Z,RASTER_BOTH
                RET
RASTER_SKIP:    LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                ADD     IX,DE
                ADD     IY,DE
                JR      RASTER_CHECK_ROW
RASTER_BITMAP:  LD      B,(HL)
                INC     HL
RASTER_BITMAP_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IX+0),A
                INC     IX
                INC     IY
                DJNZ    RASTER_BITMAP_LOOP
                JR      RASTER_CHECK_ROW
RASTER_ATTRIBUTE:
                LD      B,(HL)
                INC     HL
RASTER_ATTRIBUTE_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IY+0),A
                INC     IX
                INC     IY
                DJNZ    RASTER_ATTRIBUTE_LOOP
                JR      RASTER_CHECK_ROW
RASTER_BOTH:    LD      B,(HL)
                INC     HL
RASTER_BOTH_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IX+0),A
                LD      A,(HL)
                INC     HL
                LD      (IY+0),A
                INC     IX
                INC     IY
                DJNZ    RASTER_BOTH_LOOP
RASTER_CHECK_ROW:
                PUSH    IY
                POP     DE
                LD      A,E
                AND     $1F
                JR      NZ,RASTER_COMMAND
                LD      A,D
                CP      $78
                RET     Z
                PUSH    HL
                LD      HL,(NEXT_ROW_PTR)
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      (NEXT_ROW_PTR),HL
                PUSH    DE
                POP     IX
                SET     5,D
                PUSH    DE
                POP     IY
                POP     HL
                JR      RASTER_COMMAND

TABLE_PTR:      DW      0
FRAMES_LEFT:    DB      0
NEXT_TICK:      DB      0
SCHED_ACC:      DW      0
NEXT_ROW_PTR:   DW      0
ORIGINAL_SP:    DW      0
PACK_COUNT:     DB      0
PACK_VALUE:     DB      0

BITMAP_ROWS:
                INCLUDE "bitmap_rows.inc"

                INCLUDE "tap_frames.inc"
