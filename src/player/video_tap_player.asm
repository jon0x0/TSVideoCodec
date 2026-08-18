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
                CALL    AUDIO_INIT
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
                JR      Z,COPY_RASTER
                CP      9
                JP      NZ,EXIT_PLAYER
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                EX      DE,HL
                CALL    DECODE_PAIRED_XOR
                JR      FRAME_READY
COPY_RASTER:
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

FRAME_READY:    CALL    AUDIO_TRIGGER_FRAME
                EI
HOLD:           HALT
                DI
                CALL    AUDIO_SERVICE
                EI
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
                JP      NZ,NEXT_FRAME

PAUSE_LAST:     CALL    RESET_LOOP
                JP      NEXT_FRAME

RESET_SEQUENCE: LD      HL,FRAME_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                XOR     A
                LD      (PLAYBACK_FRAME),A
                RET

RESET_LOOP:     LD      HL,LOOP_TABLE_PTRS
                LD      (TABLE_PTR),HL
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                XOR     A
                LD      (PLAYBACK_FRAME),A
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

AUDIO_TRIGGER_FRAME:
                LD      A,(PLAYBACK_FRAME)
                LD      E,A
                LD      D,0
                LD      HL,AUDIO_EVENT_TABLE
                ADD     HL,DE
                LD      C,(HL)
                INC     A
                CP      FRAME_COUNT
                JR      C,TAP_AUDIO_FRAME_STORED
                XOR     A
TAP_AUDIO_FRAME_STORED:
                LD      (PLAYBACK_FRAME),A
                LD      A,C
                OR      A
                RET     Z
                DEC     A
                LD      E,A
                LD      D,0
                LD      H,D
                LD      L,E
                ADD     HL,HL
                LD      DE,AUDIO_SOUND_TABLE
                ADD     HL,DE
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                LD      A,(DE)
                INC     DE
                LD      (AUDIO_CHANNELS),A
                LD      A,(DE)
                INC     DE
                LD      (AUDIO_PERIOD),A
                LD      A,(DE)
                INC     DE
                LD      L,A
                LD      A,(DE)
                INC     DE
                LD      H,A
                LD      (AUDIO_BLOCKS),HL
                LD      (AUDIO_PTR),DE
                LD      A,1
                LD      (AUDIO_DELAY),A
                LD      (AUDIO_ACTIVE),A
                RET

AUDIO_SERVICE:  CALL    CHECK_SOUND_KEY
                JP      AUDIO_TICK

CHECK_SOUND_KEY:
                PUSH    AF
                PUSH    BC
                PUSH    HL
                LD      BC,$FDFE
                IN      A,(C)
                BIT     1,A
                JR      NZ,TAP_SOUND_KEY_RELEASED
                LD      A,(AUDIO_S_LATCH)
                OR      A
                JR      NZ,TAP_SOUND_KEY_DONE
                LD      A,1
                LD      (AUDIO_S_LATCH),A
                LD      A,(AUDIO_ENABLED_STATE)
                XOR     1
                LD      (AUDIO_ENABLED_STATE),A
                OR      A
                JR      NZ,TAP_SOUND_KEY_DONE
                CALL    AUDIO_SILENCE
                JR      TAP_SOUND_KEY_DONE
TAP_SOUND_KEY_RELEASED:
                XOR     A
                LD      (AUDIO_S_LATCH),A
TAP_SOUND_KEY_DONE:
                POP     HL
                POP     BC
                POP     AF
                RET

AUDIO_TICK:     PUSH    AF
                PUSH    BC
                PUSH    DE
                PUSH    HL
                LD      A,(AUDIO_ACTIVE)
                OR      A
                JR      Z,TAP_AUDIO_TICK_DONE
                LD      A,(AUDIO_DELAY)
                DEC     A
                LD      (AUDIO_DELAY),A
                JR      NZ,TAP_AUDIO_TICK_DONE
                LD      HL,(AUDIO_BLOCKS)
                LD      A,H
                OR      L
                JR      Z,TAP_AUDIO_EXPIRED
                LD      HL,(AUDIO_PTR)
                LD      A,(AUDIO_CHANNELS)
                LD      B,A
TAP_AUDIO_CHANNEL_LOOP:
                PUSH    BC
                LD      A,(AUDIO_ENABLED_STATE)
                OR      A
                JR      Z,TAP_AUDIO_CHANNEL_MUTED
                LD      E,B
                LD      A,B
                DEC     A
                ADD     A,A
                OUT     ($F5),A
                LD      D,A
                LD      A,(HL)
                INC     HL
                OUT     ($F6),A
                LD      A,D
                INC     A
                OUT     ($F5),A
                LD      D,A
                LD      A,(HL)
                AND     $0F
                OUT     ($F6),A
                LD      A,E
                ADD     A,7
                OUT     ($F5),A
                LD      A,(HL)
                RRCA
                RRCA
                RRCA
                RRCA
                AND     $0F
                OUT     ($F6),A
                INC     HL
                JR      TAP_AUDIO_CHANNEL_DONE
TAP_AUDIO_CHANNEL_MUTED:
                INC     HL
                INC     HL
TAP_AUDIO_CHANNEL_DONE:
                POP     BC
                DJNZ    TAP_AUDIO_CHANNEL_LOOP
                LD      (AUDIO_PTR),HL
                LD      HL,(AUDIO_BLOCKS)
                DEC     HL
                LD      (AUDIO_BLOCKS),HL
                LD      A,(AUDIO_PERIOD)
                LD      (AUDIO_DELAY),A
                JR      TAP_AUDIO_TICK_DONE
TAP_AUDIO_EXPIRED:
                XOR     A
                LD      (AUDIO_ACTIVE),A
                CALL    AUDIO_SILENCE
TAP_AUDIO_TICK_DONE:
                POP     HL
                POP     DE
                POP     BC
                POP     AF
                RET

AUDIO_INIT:     XOR     A
                LD      (AUDIO_ACTIVE),A
                LD      (AUDIO_S_LATCH),A
                INC     A
                LD      (AUDIO_ENABLED_STATE),A
                LD      A,7
                OUT     ($F5),A
                LD      A,56
                OUT     ($F6),A
AUDIO_SILENCE:  XOR     A
                LD      B,3
                LD      C,8
TAP_AUDIO_SILENCE_LOOP:
                LD      A,C
                OUT     ($F5),A
                XOR     A
                OUT     ($F6),A
                INC     C
                DJNZ    TAP_AUDIO_SILENCE_LOOP
                RET

; NZ means a non-S key is held. S alone is reserved for sound toggling.
KEY_PRESSED:    XOR     A
                IN      A,($FE)
                CPL
                AND     $1F
                LD      C,A
                AND     $1D                ; any column other than S's column
                RET     NZ
                LD      A,C
                AND     2
                RET     Z
                LD      BC,$02FE           ; all rows except A/S/D/F/G
                IN      A,(C)
                CPL
                AND     2
                RET

; Restore normal display, BASIC's stack, and return from RANDOMIZE USR.
EXIT_PLAYER:    DI
                CALL    AUDIO_SILENCE
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

; Reversible paired cells: count u16, then offset u16, flags, and selected
; bitmap/attribute XOR masks. HL is the contiguous source pointer.
DECODE_PAIRED_XOR:
                LD      A,(HL)
                INC     HL
                LD      (PAIRS_LEFT),A
                LD      A,(HL)
                INC     HL
                LD      (PAIRS_LEFT+1),A
TAP_PAIR_XOR_LOOP:
                LD      DE,(PAIRS_LEFT)
                LD      A,D
                OR      E
                RET     Z
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                SET     6,D
                LD      A,(HL)
                INC     HL
                LD      C,A
                PUSH    HL
                EX      DE,HL
                POP     DE
                BIT     0,C
                JR      Z,TAP_PAIR_XOR_ATTRIBUTE
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
TAP_PAIR_XOR_ATTRIBUTE:
                BIT     1,C
                JR      Z,TAP_PAIR_XOR_NEXT
                SET     5,H
                LD      A,(DE)
                INC     DE
                XOR     (HL)
                LD      (HL),A
TAP_PAIR_XOR_NEXT:
                PUSH    DE
                POP     HL
                LD      DE,(PAIRS_LEFT)
                DEC     DE
                LD      (PAIRS_LEFT),DE
                JR      TAP_PAIR_XOR_LOOP

TABLE_PTR:      DW      0
FRAMES_LEFT:    DB      0
NEXT_TICK:      DB      0
SCHED_ACC:      DW      0
NEXT_ROW_PTR:   DW      0
ORIGINAL_SP:    DW      0
PACK_COUNT:     DB      0
PACK_VALUE:     DB      0
PAIRS_LEFT:     DW      0
PLAYBACK_FRAME: DB      0
AUDIO_ACTIVE:   DB      0
AUDIO_PTR:      DW      0
AUDIO_BLOCKS:   DW      0
AUDIO_CHANNELS: DB      0
AUDIO_PERIOD:   DB      0
AUDIO_DELAY:    DB      0
AUDIO_ENABLED_STATE: DB 1
AUDIO_S_LATCH:  DB      0

BITMAP_ROWS:
                INCLUDE "bitmap_rows.inc"

                INCLUDE "tap_frames.inc"
                INCLUDE "audio2ay_config.inc"
