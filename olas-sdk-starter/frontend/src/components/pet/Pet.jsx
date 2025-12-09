import React, { useEffect, useMemo, useState } from 'react';

const ASSETS_BASE_URL = 'https://storage.googleapis.com/pettai_assets';
const EMOTION_THRESHOLDS = [30, 50, 85];

function calculateBaseEmotion(pet) {
  if (!pet) return 'happy';

  const happiness = Number(pet?.PetStats?.happiness ?? 100);
  const health = Number(pet?.PetStats?.health ?? 100);
  const hunger = Number(pet?.PetStats?.hunger ?? 100);
  const hygiene = Number(pet?.PetStats?.hygiene ?? 100);
  const energy = Number(pet?.PetStats?.energy ?? 100);

  if (pet.sleeping) return 'sleep';
  if (pet.dead) return 'dead';

  let emotion = 'happy';

  if (
    happiness < EMOTION_THRESHOLDS[2] ||
    health < EMOTION_THRESHOLDS[2] ||
    hunger < EMOTION_THRESHOLDS[2] ||
    hygiene < EMOTION_THRESHOLDS[2] ||
    energy < EMOTION_THRESHOLDS[2]
  ) {
    emotion = 'normal';
  }

  if (happiness < EMOTION_THRESHOLDS[1]) emotion = 'sad';
  if (happiness < EMOTION_THRESHOLDS[0]) emotion = 'very_sad';
  if (health < EMOTION_THRESHOLDS[0]) emotion = 'sick';

  return emotion;
}

function shouldShowStinky(pet) {
  if (!pet) return false;
  const hygiene = Number(pet?.PetStats?.hygiene ?? 100);
  return hygiene < EMOTION_THRESHOLDS[0];
}

function getLayerStyle(layer) {
  const defaults = { scale: 1, left: '0px', top: '0px' };
  const byType = {
    handheld: { scale: 1.1, left: '10px', top: '40px' },
    head: { scale: 1.4, left: '-2px', top: '40px' },
    stinky: { scale: 1.4, left: '5px', top: '40px' },
    back: { scale: 1.4, left: '-10px', top: '40px' },
    toy: { scale: 1.4, left: '-10px', top: '40px' },
    special: { scale: 1.6, left: '-1px', top: '40px' },
  };
  return { ...defaults, ...(byType[layer.type] || {}) };
}

function generatePetLayers(pet) {
  const layers = [];

  if (shouldShowStinky(pet)) {
    layers.push({
      url: `${ASSETS_BASE_URL}/stinky.gif`,
      zIndex: 1,
      type: 'stinky',
      alt: 'Stinky overlay',
    });
  }

  const baseEmotion = calculateBaseEmotion(pet);
  layers.push({
    url: `${ASSETS_BASE_URL}/emotions/${baseEmotion}.gif`,
    zIndex: 3,
    type: 'emotion',
    alt: `Pet ${baseEmotion} emotion`,
  });

  return { layers };
}

const SIZE_TO_PX = {
  big: 230,
  medium: 120,
  small: 60,
};

export default function Pet({ name, pet, size = 'big', message, isClickable = false, onClick }) {
  const sizePx = SIZE_TO_PX[size] || SIZE_TO_PX.big;
  const scale = 1;
  const containerHeight = sizePx * scale;

  // Build layers and track load errors to show fallback if all fail
  const { layers } = useMemo(() => generatePetLayers(pet), [pet]);
  const [errorCount, setErrorCount] = useState(0);

  const layerUrls = useMemo(() => layers.map(l => l.url).join(','), [layers]);

  useEffect(() => {
    // reset error counter when layers set changes
    setErrorCount(0);
  }, [layerUrls]);

  const showFallback = layers.length === 0 || errorCount >= layers.length;

  const displayName = name || (pet && pet.name) || '';
  const resolvedMessage = message ?? (displayName ? `Hi, I'm ${displayName}!` : null);

  return (
    <div className="shrink-0">

      <div
        className={`pet__image flex justify-center relative ${isClickable ? 'cursor-pointer' : ''}`}
        style={{
          height: `${containerHeight}px`,
          minHeight: `${containerHeight}px`,
          width: `${sizePx}px`,
          minWidth: `${sizePx}px`,
        }}
        onClick={isClickable ? onClick : undefined}
      >
        <div
          className="overflow-hidden block relative w-full"
          style={{
            height: `${sizePx * 1.43}px`,
            top: `-${containerHeight * 0.2}px`,
          }}
        >
          {layers.map((layer, index) => (
            <img
              key={`${layer.type}-${index}`}
              className="absolute left-0 w-full object-contain"
              src={layer.url}
              alt={layer.alt}
              style={{
                zIndex: layer.zIndex,
                height: `${sizePx * 1.2}px`,
                ...(() => {
                  const s = getLayerStyle(layer);
                  return { transform: `scale(${s.scale})`, left: s.left, top: s.top };
                })(),
              }}
              onError={e => {
                // hide broken layer
                e.currentTarget.style.display = 'none';
                setErrorCount(c => c + 1);
              }}
            />
          ))}

          {showFallback && (
            <div className="absolute left-1/2 top-1/2 transform -translate-x-1/2 -translate-y-1/2 text-center">
              <div className="text-4xl mb-2">🐧</div>
              <div className="text-xs text-gray-500">Pet Preview</div>
            </div>
          )}
        </div>

        {resolvedMessage && (
          <div
            className="pet__chat text-xs font-bold py-1.5 px-3 rounded-full absolute -translate-x-1/2 -translate-y-1/2 left-1/2 bg-white text-semantic-accent-bold z-100 overflow-visible"
            style={{
              boxShadow: '-1.148px 2.295px 9.869px 0px rgba(0, 0, 0, 0.23)',
              top: size === 'big' ? '120px' : '75px',
              marginLeft: size === 'big' ? '-80px' : '-40px',
              zIndex: 20,
            }}
          >
            <div>{resolvedMessage}</div>
            <div className="pet__chat--svg absolute right-[25px] z-[-1]" style={{ top: 'calc(100% - 4px)' }}>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="10" viewBox="0 0 12 10" fill="none">
                <path d="M7.92769 8.73426C7.04434 10.2643 4.83597 10.2643 3.95263 8.73426L0.971335 3.57051C0.0879897 2.04051 1.19217 0.128007 2.95886 0.128007L8.92145 0.128007C10.6881 0.128008 11.7923 2.04051 10.909 3.57051L7.92769 8.73426Z" fill="white" />
              </svg>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}




