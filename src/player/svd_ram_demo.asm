; Autostart RAM/TAP demonstration harness for provisional SVD v0 ECM playback.
; The build script supplies bitmap_rows.inc and demo.svd through its include path.

                ORG     $7800

PORT_CTRL       EQU     $FF
SYS_FRAMES      EQU     $5C78       ; low byte of ROM 60 Hz frame counter
                INCLUDE "demo_config.inc"
SVD_HEADER_SIZE EQU     14

START:          DI
                LD      SP,$FF00            ; reserved above maximum admitted image
                LD      A,2
                OUT     (PORT_CTRL),A       ; native TS2068 Extended Color Mode
                LD      HL,SVD_STREAM+SVD_HEADER_SIZE
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                LD      A,1
                LD      (FIRST_FRAME),A

NEXT_FRAME:
                LD      A,(HL)              ; frame type
                LD      DE,5                ; type + u32 payload length
                ADD     HL,DE
                CP      FRAME_KEY
                JR      Z,PLAY_KEY
                CP      FRAME_DELTA
                JR      Z,PLAY_DELTA
                CP      FRAME_REPEAT
                JR      Z,FRAME_READY
                CP      FRAME_SPARSE
                JR      Z,PLAY_SPARSE
                JR      PLAYER_ERROR

PLAY_KEY:       CALL    DECODE_KEY
                JR      FRAME_READY

PLAY_DELTA:     CALL    DECODE_DELTA
                JR      C,PLAYER_ERROR

PLAY_SPARSE:    CALL    DECODE_SPARSE
                JR      FRAME_READY

FRAME_READY:    LD      A,(FIRST_FRAME)
                OR      A
                JR      Z,WAIT_SCHEDULE
                XOR     A
                LD      (FIRST_FRAME),A
                LD      A,(SYS_FRAMES)
                ADD     A,4                 ; calibrated to five ready intervals
                LD      (NEXT_TICK),A
WAIT_SCHEDULE:  EI
WAIT_FRAME:     HALT
                LD      A,(SYS_FRAMES)
                LD      B,A
                LD      A,(NEXT_TICK)
                CP      B
                JR      NZ,WAIT_FRAME
                DI
                ADD     A,4                 ; HALT quantization yields ~12 Hz ready cadence
                LD      (NEXT_TICK),A
                LD      A,(FRAMES_LEFT)
                DEC     A
                LD      (FRAMES_LEFT),A
                JR      NZ,NEXT_FRAME

; HOLD_LAST remains a stable validation breakpoint. In normal playback it
; pauses on the final reconstructed frame for about one second, then restarts
; at the keyframe and repeats indefinitely.
HOLD_LAST:      EI
                LD      B,60
PAUSE_LAST:     HALT
                DJNZ    PAUSE_LAST
                DI
                LD      HL,SVD_STREAM+SVD_HEADER_SIZE
                LD      A,FRAME_COUNT
                LD      (FRAMES_LEFT),A
                LD      A,1
                LD      (FIRST_FRAME),A
                JP      NEXT_FRAME

PLAYER_ERROR:   LD      A,2                 ; red border indicates stream error
                OUT     ($FE),A
                EI
ERROR_HOLD:     HALT
                JR      ERROR_HOLD

FRAMES_LEFT:    DB      0
FIRST_FRAME:    DB      0
NEXT_TICK:      DB      0

                ORG     $8000
                INCLUDE "svd_decoder.asm"

SVD_STREAM:
                INCBIN  "demo.svd"
