; TS2068 AROS cartridge player for banked raw ECM frames.

                ORG     $8000

                DB      $02,$02,$08,$80,$EF,$01,$00,$00

                INCLUDE "player_config.inc"

PORT_HSR        EQU     $F4
PORT_CTRL       EQU     $FF
SYS_FRAMES      EQU     $5C78
TABLE_PTR       EQU     $7800
FRAMES_LEFT     EQU     $7802
NEXT_TICK       EQU     $7803
SCHED_ACC       EQU     $7804
NEXT_ROW_PTR    EQU     $7806
FIRST_DECODE    EQU     $7808
ROWS_LEFT       EQU     $7809
PAIRS_LEFT      EQU     $780A
PACK_COUNT      EQU     $780C
PACK_VALUE      EQU     $780D
FIFO_SLOT       EQU     $780E

START:          DI
                POP     HL                  ; move AROS return off screen RAM
                LD      SP,$7FFF
                PUSH    HL
                LD      HL,0
                LD      (SCHED_ACC),HL
                CALL    PRELOAD_SHADOW
                LD      HL,$6000           ; hide keyframe construction
                LD      DE,$6001
                LD      BC,$17FF
                LD      (HL),0
                LDIR
                LD      A,2
                OUT     (PORT_CTRL),A
                CALL    RESET_SEQUENCE

NEXT_FRAME:     CALL    NEXT_INTERVAL
                LD      B,A
                LD      A,(FIRST_DECODE)
                OR      A
                JR      NZ,FIRST_INTERVAL
                LD      A,B
                SUB     DECODE_TICK_COMPENSATION
                LD      B,A
                JR      INTERVAL_READY
FIRST_INTERVAL: XOR     A
                LD      (FIRST_DECODE),A
INTERVAL_READY:
                LD      A,(SYS_FRAMES)
                ADD     A,B
                LD      (NEXT_TICK),A
                LD      HL,(TABLE_PTR)
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      (TABLE_PTR),HL
                PUSH    DE
                POP     IX
                CALL    COPY_FRAME

FRAME_READY:    EI
HOLD:           HALT
                LD      A,(SYS_FRAMES)
                LD      B,A
                LD      A,(NEXT_TICK)
                SUB     B
                JR      Z,HOLD_DONE
                JP      M,HOLD_DONE         ; decoder missed deadline
                JR      HOLD
HOLD_DONE:
                DI
                LD      A,(FRAMES_LEFT)
                DEC     A
                LD      (FRAMES_LEFT),A
                JR      NZ,NEXT_FRAME

                EI
PAUSE_LAST:     LD      A,STOP_AT_END
                OR      A
                JR      Z,PAUSE_RESTART
STOP_FOREVER:   HALT
                JR      STOP_FOREVER
PAUSE_RESTART:  LD      B,LOOP_PAUSE_FRAMES
                LD      A,B
                OR      A
                JR      Z,LOOP_RESTART
PAUSE_LOOP:     HALT
                DJNZ    PAUSE_LOOP
LOOP_RESTART:
                DI
                CALL    RESET_LOOP
                JR      NEXT_FRAME

RESET_SEQUENCE: LD      HL,FRAME_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                LD      A,1
                LD      (FIRST_DECODE),A
                RET

RESET_LOOP:     LD      HL,LOOP_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                RET

; Fractional 60 Hz scheduler. Keeps its remainder across loop boundaries.
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

; Cartridge chunks 2/3 occupy the display addresses. Preserve their payload
; in underlying HOME RAM chunks 6/7, then restore the live HOME display.
PRELOAD_SHADOW: LD      A,$1C               ; cartridge chunks 2,3,4
                OUT     (PORT_HSR),A
                LD      HL,$4000
                LD      DE,$C000
                LD      BC,$4000
                LDIR
                LD      A,$10               ; code chunk only
                OUT     (PORT_HSR),A
                RET

; IX addresses records: mask, source, destination, length; zero mask ends.
COPY_FRAME:     LD      A,(IX+0)            ; 1=key table, 2=XOR, 3=hybrid
                INC     IX
                CP      3
                JP      Z,COPY_HYBRID
                CP      4
                JP      Z,COPY_RASTER
                CP      5
                JP      Z,COPY_ROW_HYBRID
                CP      6
                JP      Z,COPY_PAIRED
                CP      7
                JP      Z,COPY_PACK_KEY
                CP      8
                JP      Z,COPY_FIFO_HYBRID
                CP      9
                JP      Z,COPY_PAIRED_XOR
                CP      2
                JP      Z,COPY_XOR
COPY_KEY:       LD      A,(IX+0)
                INC     IX
                OR      A
                JP      Z,COPY_DONE
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      L,(IX+0)
                LD      H,(IX+1)
                LD      E,(IX+2)
                LD      D,(IX+3)
                LD      C,(IX+4)
                LD      B,(IX+5)
                LD      A,6
ADVANCE_IX:     INC     IX
                DEC     A
                JR      NZ,ADVANCE_IX
                LDIR
                JR      COPY_KEY

; Two PackBits planes: bitmap then attributes. Each table record is mask,source.
COPY_PACK_KEY:  LD      A,(IX+0)
                LD      BC,$00F4
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                LD      HL,$4000
                LD      BC,$1800
                CALL    DECODE_PACKBITS
                LD      A,(IX+3)
                LD      BC,$00F4
                OUT     (C),A
                LD      E,(IX+4)
                LD      D,(IX+5)
                LD      HL,$6000
                LD      BC,$1800
                CALL    DECODE_PACKBITS
                JP      COPY_DONE

; DE=packed source, HL=destination, BC=exact output length.
; 0..127: 1..128 literals; 128..255: 3..130 repeated bytes.
DECODE_PACKBITS:
PACK_NEXT:      LD      A,(DE)
                INC     DE
                BIT     7,A
                JR      NZ,PACK_RUN
                INC     A
                LD      (PACK_COUNT),A
PACK_LITERAL:   LD      A,(DE)
                INC     DE
                LD      (HL),A
                INC     HL
                DEC     BC
                LD      A,(PACK_COUNT)
                DEC     A
                LD      (PACK_COUNT),A
                JR      NZ,PACK_LITERAL
                JR      PACK_CHECK
PACK_RUN:       AND     $7F
                ADD     A,3
                LD      (PACK_COUNT),A
                LD      A,(DE)
                INC     DE
                LD      (PACK_VALUE),A
PACK_RUN_LOOP:  LD      A,(PACK_VALUE)
                LD      (HL),A
                INC     HL
                DEC     BC
                LD      A,(PACK_COUNT)
                DEC     A
                LD      (PACK_COUNT),A
                JR      NZ,PACK_RUN_LOOP
PACK_CHECK:     LD      A,B
                OR      C
                RET     Z
                JR      PACK_NEXT

COPY_XOR:       LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                LD      HL,$4000
                CALL    DECODE_XOR_PLANE
                LD      HL,$6000
                CALL    DECODE_XOR_PLANE
                JP      COPY_DONE

COPY_HYBRID:    LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                LD      HL,$4000
                CALL    DECODE_HYBRID_PLANE
                LD      HL,$6000
                CALL    DECODE_HYBRID_PLANE
                JP      COPY_DONE

; Logical FIFO hybrid stream. The table contains slot index and source address.
COPY_FIFO_HYBRID:
                LD      A,(IX+0)
                CALL    FIFO_SELECT
                LD      E,(IX+1)
                LD      D,(IX+2)
                LD      HL,$4000
                CALL    DECODE_FIFO_HYBRID_PLANE
                LD      HL,$6000
                CALL    DECODE_FIFO_HYBRID_PLANE
                JP      COPY_DONE

; A=logical payload slot 0..6. Select its cartridge/shadow mapping.
FIFO_SELECT:    LD      (FIFO_SLOT),A
                PUSH    HL
                PUSH    DE
                PUSH    BC
                LD      E,A
                LD      D,0
                LD      HL,FIFO_MASKS
                ADD     HL,DE
                LD      A,(HL)
                LD      BC,$00F4
                OUT     (C),A
                POP     BC
                POP     DE
                POP     HL
                RET

FIFO_NEXT:      LD      A,(FIFO_SLOT)
                INC     A
                CALL    FIFO_SELECT
                LD      A,(FIFO_SLOT)
                CP      1
                JR      Z,FIFO_SOURCE_2000
                CP      2
                JR      Z,FIFO_SOURCE_C000
                CP      3
                JR      Z,FIFO_SOURCE_E000
                CP      4
                JR      Z,FIFO_SOURCE_A000
                CP      5
                JR      Z,FIFO_SOURCE_C000
                ; slot 6, or defensive wrap
FIFO_SOURCE_E000:
                LD      DE,$E000
                RET
FIFO_SOURCE_2000:
                LD      DE,$2000
                RET
FIFO_SOURCE_C000:
                LD      DE,$C000
                RET
FIFO_SOURCE_A000:
                LD      DE,$A000
                RET

; Direct hybrid decoder with command-boundary bank markers.
; C1=switch bank, C2=skip one padding byte then switch bank.
DECODE_FIFO_HYBRID_PLANE:
                LD      A,(DE)
                INC     DE
                OR      A
                RET     Z
                JP      P,FIFO_HYBRID_SKIP
                CP      $C0
                JR      Z,FIFO_HYBRID_MASK
                CP      $C1
                JR      Z,FIFO_HYBRID_NEXT_BANK
                CP      $C2
                JR      Z,FIFO_HYBRID_PADDED_BANK
                AND     $3F
                INC     A
                LD      B,A
FIFO_HYBRID_LITERAL_LOOP:
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
                INC     HL
                DJNZ    FIFO_HYBRID_LITERAL_LOOP
                JR      DECODE_FIFO_HYBRID_PLANE
FIFO_HYBRID_SKIP:
                LD      C,A
                LD      B,0
                ADD     HL,BC
                JR      DECODE_FIFO_HYBRID_PLANE
FIFO_HYBRID_MASK:
                LD      A,(DE)
                INC     DE
                LD      C,A
                LD      B,8
FIFO_HYBRID_MASK_LOOP:
                SLA     C
                JR      NC,FIFO_HYBRID_MASK_NEXT
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
FIFO_HYBRID_MASK_NEXT:
                INC     HL
                DJNZ    FIFO_HYBRID_MASK_LOOP
                JR      DECODE_FIFO_HYBRID_PLANE
FIFO_HYBRID_PADDED_BANK:
                INC     DE
FIFO_HYBRID_NEXT_BANK:
                CALL    FIFO_NEXT
                JR      DECODE_FIFO_HYBRID_PLANE

COPY_RASTER:    LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                PUSH    DE
                POP     HL
                CALL    DECODE_RASTER
                JP      COPY_DONE

COPY_ROW_HYBRID:
                LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                LD      IX,BITMAP_ROWS
                LD      A,192
                LD      (ROWS_LEFT),A
ROW_HYBRID_LOOP:
                LD      L,(IX+0)
                LD      H,(IX+1)
                INC     IX
                INC     IX
                PUSH    HL
                CALL    DECODE_HYBRID_PLANE
                POP     HL
                SET     5,H
                CALL    DECODE_HYBRID_PLANE
                LD      A,(ROWS_LEFT)
                DEC     A
                LD      (ROWS_LEFT),A
                JR      NZ,ROW_HYBRID_LOOP
                JP      COPY_DONE

COPY_PAIRED:    LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                CALL    DECODE_PAIRED
                JP      COPY_DONE

COPY_PAIRED_XOR:
                LD      A,(IX+0)
                LD      C,$F4
                LD      B,0
                OUT     (C),A
                LD      E,(IX+1)
                LD      D,(IX+2)
                CALL    DECODE_PAIRED_XOR
                JP      COPY_DONE

; Raster-ordered changed cells. Each record is offset, flags, then replacement
; bitmap and/or attribute. The two visible planes are therefore never decoded
; in separate passes.
DECODE_PAIRED:  LD      A,(DE)
                INC     DE
                LD      (PAIRS_LEFT),A
                LD      A,(DE)
                INC     DE
                LD      (PAIRS_LEFT+1),A
PAIRED_LOOP:    LD      A,(PAIRS_LEFT)
                LD      C,A
                LD      A,(PAIRS_LEFT+1)
                OR      C
                RET     Z
                LD      A,(DE)
                LD      L,A
                INC     DE
                LD      A,(DE)
                LD      H,A
                INC     DE
                SET     6,H
                LD      A,(DE)
                INC     DE
                LD      C,A
                BIT     0,C
                JR      Z,PAIRED_ATTRIBUTE
                LD      A,(DE)
                INC     DE
                LD      (HL),A
PAIRED_ATTRIBUTE:
                BIT     1,C
                JR      Z,PAIRED_NEXT
                SET     5,H
                LD      A,(DE)
                INC     DE
                LD      (HL),A
PAIRED_NEXT:    LD      HL,(PAIRS_LEFT)
                DEC     HL
                LD      (PAIRS_LEFT),HL
                LD      A,H
                OR      L
                JP      NZ,PAIRED_LOOP
                RET

; Reversible paired cells. Same layout as DECODE_PAIRED, but values are XOR
; masks rather than replacements, so one record works in either direction.
DECODE_PAIRED_XOR:
                LD      A,(DE)
                INC     DE
                LD      (PAIRS_LEFT),A
                LD      A,(DE)
                INC     DE
                LD      (PAIRS_LEFT+1),A
PAIRED_XOR_LOOP:
                LD      HL,(PAIRS_LEFT)
                LD      A,H
                OR      L
                RET     Z
                LD      A,(DE)
                LD      L,A
                INC     DE
                LD      A,(DE)
                LD      H,A
                INC     DE
                SET     6,H
                LD      A,(DE)
                INC     DE
                LD      C,A
                BIT     0,C
                JR      Z,PAIRED_XOR_ATTRIBUTE
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
PAIRED_XOR_ATTRIBUTE:
                BIT     1,C
                JR      Z,PAIRED_XOR_NEXT
                SET     5,H
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
PAIRED_XOR_NEXT:
                LD      HL,(PAIRS_LEFT)
                DEC     HL
                LD      (PAIRS_LEFT),HL
                JR      PAIRED_XOR_LOOP

; Raster-ordered replacement commands. Each run is confined to one 8x1 row;
; bitmap and ECM attribute bytes are updated together when both changed.
; 00=end, 01=skip u16, 02=bitmap, 03=attribute, 04=paired; runs use u8.
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
RASTER_BITMAP: LD      B,(HL)
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

; 00=end, 01..7F=skip, 80..BF=literal XOR, C0=8-byte sparse mask.
DECODE_HYBRID_PLANE:
                LD      A,(DE)
                INC     DE
                OR      A
                RET     Z
                JP      P,HYBRID_SKIP
                CP      $C0
                JR      Z,HYBRID_MASK
                AND     $3F
                INC     A
                LD      B,A
HYBRID_LITERAL_LOOP:
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
                INC     HL
                DJNZ    HYBRID_LITERAL_LOOP
                JR      DECODE_HYBRID_PLANE
HYBRID_SKIP:    LD      C,A
                LD      B,0
                ADD     HL,BC
                JR      DECODE_HYBRID_PLANE
HYBRID_MASK:    LD      A,(DE)
                INC     DE
                LD      C,A
                LD      B,8
HYBRID_MASK_LOOP:
                SLA     C
                JR      NC,HYBRID_MASK_NEXT
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
HYBRID_MASK_NEXT:
                INC     HL
                DJNZ    HYBRID_MASK_LOOP
                JR      DECODE_HYBRID_PLANE

; DE is compressed source, HL is one 6144-byte display plane.
; 00=end, 01..7F=skip, 80..FF=1..128 literal XOR bytes.
DECODE_XOR_PLANE:
                LD      A,(DE)
                INC     DE
                OR      A
                RET     Z
                JP      M,XOR_LITERAL
                LD      C,A
                LD      B,0
                ADD     HL,BC
                JR      DECODE_XOR_PLANE
XOR_LITERAL:    AND     $7F
                INC     A
                LD      B,A
XOR_LOOP:       LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
                INC     HL
                DJNZ    XOR_LOOP
                JR      DECODE_XOR_PLANE
COPY_DONE:      LD      A,$10               ; code chunk only; HOME ROM/RAM elsewhere
                LD      BC,$00F4
                OUT     (C),A
                RET

FIFO_MASKS:     DB      $11,$12,$10,$10,$30,$50,$90

BITMAP_ROWS:
                INCLUDE "bitmap_rows.inc"

                INCLUDE "frame_table.inc"
                DEFS    $A000-$,$FF
